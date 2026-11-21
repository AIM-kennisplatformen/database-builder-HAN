"""Persist document metadata, observations, and inventory cursors."""

from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import asdict
from json import dumps
from sys import exc_info
from typing import cast, override
from uuid import UUID, uuid4

import anyio
from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import TypeAdapter

from domain.exceptions.metadata_ingest_errors import MetadataRunAlreadyActive
from domain.models.documents.document import Document, DocumentMetadata
from domain.models.documents.metadata_selection import MetadataSelection
from domain.ports.documents.document_store import (
    CombinedDocumentRefreshStore,
    DocumentStore,
    MetadataIngestRun,
    MetadataRunPublication,
)

_metadata_adapter = TypeAdapter(DocumentMetadata)
_METADATA_RESET_LOCK = 7368369943385156103


@asynccontextmanager
async def _staging_transaction(
    pool: AsyncConnectionPool,
) -> AsyncGenerator[AsyncConnection[tuple[object, ...]]]:
    """Keep metadata staging SQL cancellable and shield transaction teardown."""
    stack = AsyncExitStack()
    try:
        connection = await stack.enter_async_context(pool.connection())
        _ = await stack.enter_async_context(connection.transaction())
    except BaseException as exc:
        with anyio.CancelScope(shield=True):
            _ = await stack.__aexit__(type(exc), exc, exc.__traceback__)
        raise

    try:
        yield connection
        await anyio.lowlevel.checkpoint()
    except BaseException as exc:
        with anyio.CancelScope(shield=True):
            _ = await stack.__aexit__(type(exc), exc, exc.__traceback__)
        raise
    else:
        with anyio.CancelScope(shield=True):
            _ = await stack.__aexit__(None, None, None)


class PostgresDocumentStore(DocumentStore, CombinedDocumentRefreshStore):
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool: AsyncConnectionPool = pool

    async def delete_all_metadata(self) -> bool:
        async with _staging_transaction(self._pool) as connection:
            _ = await connection.execute(
                "SELECT pg_advisory_xact_lock(%s)", (_METADATA_RESET_LOCK,)
            )
            cursor = await connection.execute(
                """SELECT id FROM operation_runs
                   WHERE kind = 'metadata.ingest' AND status = 'running'
                   FOR UPDATE"""
            )
            if await cursor.fetchone() is not None:
                return False
            cursor = await connection.execute(
                """SELECT id FROM document_metadata_runs
                   WHERE status = 'running' FOR UPDATE"""
            )
            if await cursor.fetchone() is not None:
                return False
            _ = await connection.execute("DELETE FROM document_metadata_cursors")
            _ = await connection.execute("DELETE FROM document_metadata_observations")
            _ = await connection.execute("DELETE FROM document_metadata_runs")
            _ = await connection.execute("DELETE FROM document_ms_graph_sources")
            _ = await connection.execute(
                "DELETE FROM operation_runs WHERE kind = 'metadata.ingest'"
            )
            return True

    @override
    async def save_document(self, document: Document) -> None:
        file = document.file_metadata
        drive_id, item_id = document.id.id
        async with self._pool.connection() as connection:
            _ = await connection.execute(
                """
                INSERT INTO document_ms_graph_sources (
                    drive_id, item_id, name, mime_type, size_bytes,
                    content_version, source_url, sha1, sha256, quick_xor,
                    document_metadata
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (drive_id, item_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    mime_type = EXCLUDED.mime_type,
                    size_bytes = EXCLUDED.size_bytes,
                    content_version = EXCLUDED.content_version,
                    source_url = EXCLUDED.source_url,
                    sha1 = EXCLUDED.sha1,
                    sha256 = EXCLUDED.sha256,
                    quick_xor = EXCLUDED.quick_xor,
                    document_metadata = EXCLUDED.document_metadata,
                    updated_at = now()
                """,
                (
                    drive_id,
                    item_id,
                    file.name,
                    file.mime_type,
                    file.size_bytes,
                    file.content_version.version,
                    file.source_url,
                    file.hashes.sha1,
                    file.hashes.sha256,
                    file.hashes.quick_xor,
                    Jsonb(
                        _metadata_adapter.dump_python(
                            document.document_metadata, mode="json"
                        )
                    ),
                ),
            )

    @override
    async def begin_metadata_run(
        self,
        principal_id: str,
        drive_id: str,
        selection: MetadataSelection,
        operation_id: UUID | None = None,
    ) -> MetadataIngestRun:
        run_id = uuid4()
        serialized_selection = dumps(asdict(selection), sort_keys=True)
        try:
            async with self._pool.connection() as connection:
                cursor = await connection.execute(
                    """SELECT cursors.selection, cursors.delta_link
                       FROM (SELECT pg_advisory_xact_lock(%s)) AS reset_lock
                       LEFT JOIN document_metadata_cursors AS cursors
                         ON cursors.principal_id = %s AND cursors.drive_id = %s""",
                    (_METADATA_RESET_LOCK, principal_id, drive_id),
                )
                row = cast(tuple[object, ...] | None, await cursor.fetchone())
                if row is not None and row[0] is not None:
                    same_selection = row[0] == serialized_selection
                    delta_link = cast(str, row[1]) if same_selection else None
                else:
                    same_selection = False
                    delta_link = None
                _ = await connection.execute(
                    """INSERT INTO document_metadata_runs
                       (id, principal_id, drive_id, selection, status, operation_id)
                       VALUES (%s, %s, %s, %s, 'running', %s)""",
                    (
                        run_id,
                        principal_id,
                        drive_id,
                        serialized_selection,
                        operation_id,
                    ),
                )
                if operation_id is not None:
                    _ = await connection.execute(
                        """UPDATE operation_runs SET domain_ids = jsonb_set(
                             domain_ids, '{drive_run_ids}',
                             COALESCE(domain_ids->'drive_run_ids', '[]'::jsonb)
                               || jsonb_build_array(%s::text), TRUE)
                           WHERE id = %s AND status = 'running'""",
                        (str(run_id), operation_id),
                    )
        except UniqueViolation as exc:
            raise MetadataRunAlreadyActive(
                "A metadata run is already active for this operator and drive"
            ) from exc
        return MetadataIngestRun(
            run_id,
            delta_link,
            same_selection,
        )

    @override
    async def begin_group_operation(self, principal_id: str, group_id: str) -> UUID:
        operation_id = uuid4()
        async with self._pool.connection() as connection:
            _ = await connection.execute(
                "SELECT pg_advisory_xact_lock(%s)", (_METADATA_RESET_LOCK,)
            )
            _ = await connection.execute(
                """INSERT INTO operation_runs
                   (id, principal_id, kind, status, domain_ids)
                   VALUES (%s, %s, 'metadata.ingest', 'running', %s::jsonb)""",
                (
                    operation_id,
                    principal_id,
                    dumps(
                        {
                            "group_id": group_id,
                            "drive_run_ids": [],
                        }
                    ),
                ),
            )
        return operation_id

    async def _set_drive_outcome(
        self,
        connection: AsyncConnection[tuple[object, ...]],
        operation_id: UUID,
        drive_id: str,
        outcome: dict[str, object],
    ) -> None:
        cursor = await connection.execute(
            """UPDATE operation_runs
               SET summary = jsonb_set(
                 jsonb_set(summary, '{drive_outcomes}',
                   COALESCE(summary->'drive_outcomes', '{}'::jsonb), TRUE),
                 ARRAY['drive_outcomes', %s],
                 CASE WHEN summary->'drive_outcomes'->%s->>'status' IN
                   ('published', 'failed', 'skipped', 'interrupted')
                   THEN summary->'drive_outcomes'->%s
                   ELSE %s::jsonb END,
                 TRUE)
               WHERE id = %s AND status = 'running'
               RETURNING id""",
            (drive_id, drive_id, drive_id, dumps(outcome), operation_id),
        )
        if await cursor.fetchone() is None:
            raise ValueError("Combined metadata operation is not active")

    @override
    async def record_metadata_drive_outcome(
        self, operation_id: UUID, drive_id: str, outcome: dict[str, object]
    ) -> None:
        async with _staging_transaction(self._pool) as connection:
            await self._set_drive_outcome(connection, operation_id, drive_id, outcome)

    @override
    async def publish_metadata_drive(
        self,
        operation_id: UUID,
        drive_id: str,
        publication: MetadataRunPublication,
        outcome: dict[str, object],
    ) -> None:
        """Publish one drive snapshot and its parent outcome atomically."""
        async with _staging_transaction(self._pool) as connection:
            cursor = await connection.execute(
                """SELECT drive_id, operation_id FROM document_metadata_runs
                   WHERE id = %s AND status = 'running' FOR UPDATE""",
                (publication.run_id,),
            )
            row = await cursor.fetchone()
            if row is None or row != (drive_id, operation_id):
                raise ValueError(
                    "Metadata run does not belong to this combined drive operation"
                )
            await self._publish_metadata_run(connection, publication)
            await self._set_drive_outcome(
                connection,
                operation_id,
                drive_id,
                outcome,
            )

    @override
    async def complete_group_operation(
        self, operation_id: UUID, summary: dict[str, object]
    ) -> None:
        async with _staging_transaction(self._pool) as connection:
            cursor = await connection.execute(
                """UPDATE operation_runs SET status = 'complete', finished_at = now(),
                   summary = summary || %s::jsonb
                   WHERE id = %s AND status = 'running'
                   RETURNING id""",
                (dumps(summary), operation_id),
            )
            if await cursor.fetchone() is None:
                raise ValueError("Combined metadata operation is not active")

    @override
    async def fail_group_operation(
        self,
        operation_id: UUID,
        failure_code: str,
        *,
        interrupted: bool = False,
        summary: dict[str, object] | None = None,
    ) -> None:
        async with self._pool.connection() as connection:
            _ = await connection.execute(
                """UPDATE operation_runs SET status = %s, finished_at = now(),
                   failure_code = %s,
                   summary = summary || COALESCE(%s::jsonb, '{}'::jsonb)
                   WHERE id = %s AND status = 'running'""",
                (
                    "interrupted" if interrupted else "failed",
                    failure_code,
                    dumps(summary) if summary is not None else None,
                    operation_id,
                ),
            )

    @override
    async def stage_document(self, run_id: UUID, document: Document) -> None:
        file = document.file_metadata
        async with _staging_transaction(self._pool) as connection:
            _ = await connection.execute(
                """INSERT INTO document_metadata_staging (
                     run_id, item_id, visible, name, mime_type, size_bytes,
                     content_version, source_url, sha1, sha256, quick_xor,
                     document_metadata
                   ) VALUES (%s, %s, TRUE, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (run_id, item_id) DO UPDATE SET
                     visible = TRUE, name = EXCLUDED.name,
                     mime_type = EXCLUDED.mime_type, size_bytes = EXCLUDED.size_bytes,
                     content_version = EXCLUDED.content_version,
                     source_url = EXCLUDED.source_url, sha1 = EXCLUDED.sha1,
                     sha256 = EXCLUDED.sha256, quick_xor = EXCLUDED.quick_xor,
                     document_metadata = EXCLUDED.document_metadata""",
                (
                    run_id,
                    document.id.id[1],
                    file.name,
                    file.mime_type,
                    file.size_bytes,
                    file.content_version.version,
                    file.source_url,
                    file.hashes.sha1,
                    file.hashes.sha256,
                    file.hashes.quick_xor,
                    Jsonb(
                        _metadata_adapter.dump_python(
                            document.document_metadata, mode="json"
                        )
                    ),
                ),
            )

    @override
    async def stage_deleted_item(self, run_id: UUID, item_id: str) -> None:
        async with _staging_transaction(self._pool) as connection:
            _ = await connection.execute(
                """INSERT INTO document_metadata_staging (run_id, item_id, visible)
                   VALUES (%s, %s, FALSE)
                   ON CONFLICT (run_id, item_id) DO UPDATE SET visible = FALSE""",
                (run_id, item_id),
            )

    @override
    async def publish_metadata_run(
        self, run_id: UUID, delta_link: str, baseline: bool, reconcile: bool
    ) -> None:
        await self.publish_metadata_runs(
            (MetadataRunPublication(run_id, delta_link, baseline, reconcile),)
        )

    @override
    async def publish_metadata_runs(
        self,
        publications: tuple[MetadataRunPublication, ...],
        operation_id: UUID | None = None,
        summary: dict[str, object] | None = None,
        domain_ids: dict[str, object] | None = None,
    ) -> None:
        """Publish all prepared drives atomically in one database transaction."""
        if not publications and operation_id is None:
            return
        connection_context = self._pool.connection()
        connection = await connection_context.__aenter__()
        try:
            transaction_context = connection.transaction()
            _ = await transaction_context.__aenter__()
            try:
                for publication in publications:
                    await self._publish_metadata_run(connection, publication)
                if operation_id is not None:
                    _ = await connection.execute(
                        """UPDATE operation_runs SET status = 'complete', finished_at = now(),
                           summary = %s::jsonb, domain_ids = domain_ids || %s::jsonb
                           WHERE id = %s AND status = 'running'""",
                        (
                            dumps(summary or {}),
                            dumps(domain_ids or {}),
                            operation_id,
                        ),
                    )
                # Deliver a pending cancellation before entering the short commit
                # boundary. If cancelled, the transaction rolls back below.
                await anyio.lowlevel.checkpoint()
            except BaseException:
                with anyio.CancelScope(shield=True):
                    _ = await transaction_context.__aexit__(*exc_info())
                raise
            else:
                # Finish the atomic commit without allowing cancellation to leave
                # the connection in an unknown transaction state.
                with anyio.CancelScope(shield=True):
                    _ = await transaction_context.__aexit__(None, None, None)
        finally:
            # Pool release may roll back an open transaction, so protect it too.
            with anyio.CancelScope(shield=True):
                _ = await connection_context.__aexit__(*exc_info())

    async def _publish_metadata_run(
        self,
        connection: AsyncConnection[tuple[object, ...]],
        publication: MetadataRunPublication,
    ) -> None:
        run_id = publication.run_id
        delta_link = publication.delta_link
        baseline = publication.baseline
        reconcile = publication.reconcile
        # The source records, observations, cursor, and run status commit together.
        cursor = await connection.execute(
            """SELECT principal_id, drive_id, selection FROM document_metadata_runs
               WHERE id = %s AND status = 'running' FOR UPDATE""",
            (run_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError("Metadata run is not active")
        principal_id, drive_id, selection = cast(tuple[str, str, str], row)
        _ = await connection.execute(
            """INSERT INTO document_ms_graph_sources (
                 drive_id, item_id, name, mime_type, size_bytes,
                 content_version, source_url, sha1, sha256, quick_xor,
                 document_metadata
               ) SELECT %s, item_id, name, mime_type, size_bytes,
                 content_version, source_url, sha1, sha256, quick_xor,
                 document_metadata
               FROM document_metadata_staging
               WHERE run_id = %s AND visible
               ON CONFLICT (drive_id, item_id) DO UPDATE SET
                 name = EXCLUDED.name, mime_type = EXCLUDED.mime_type,
                 size_bytes = EXCLUDED.size_bytes,
                 content_version = EXCLUDED.content_version,
                 source_url = EXCLUDED.source_url, sha1 = EXCLUDED.sha1,
                 sha256 = EXCLUDED.sha256, quick_xor = EXCLUDED.quick_xor,
                 document_metadata = EXCLUDED.document_metadata,
                 updated_at = now()""",
            (drive_id, run_id),
        )
        if baseline and reconcile:
            _ = await connection.execute(
                """UPDATE document_metadata_observations AS observation
                   SET visible = FALSE, observed_run_id = %s,
                       observed_at = now()
                   WHERE principal_id = %s AND drive_id = %s
                     AND selection = %s
                     AND visible AND NOT EXISTS (
                       SELECT 1 FROM document_metadata_staging AS staged
                       WHERE staged.run_id = %s
                         AND staged.item_id = observation.item_id
                         AND staged.visible
                     )""",
                (run_id, principal_id, drive_id, selection, run_id),
            )
        _ = await connection.execute(
            """INSERT INTO document_metadata_observations
               (principal_id, drive_id, item_id, selection, visible, observed_run_id)
               SELECT %s, %s, item_id, %s, visible, %s
               FROM document_metadata_staging WHERE run_id = %s
               ON CONFLICT (principal_id, drive_id, item_id) DO UPDATE SET
                 selection = EXCLUDED.selection,
                 visible = EXCLUDED.visible,
                 observed_run_id = EXCLUDED.observed_run_id,
                 observed_at = now()""",
            (principal_id, drive_id, selection, run_id, run_id),
        )
        _ = await connection.execute(
            """INSERT INTO document_metadata_cursors
               (principal_id, drive_id, selection, delta_link, published_run_id)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (principal_id, drive_id) DO UPDATE SET
                 selection = EXCLUDED.selection,
                 delta_link = EXCLUDED.delta_link,
                 published_run_id = EXCLUDED.published_run_id""",
            (principal_id, drive_id, selection, delta_link, run_id),
        )
        _ = await connection.execute(
            "UPDATE document_metadata_runs SET status = 'complete', finished_at = now() WHERE id = %s",
            (run_id,),
        )
        _ = await connection.execute(
            "DELETE FROM document_metadata_staging WHERE run_id = %s", (run_id,)
        )

    @override
    async def discard_metadata_run(self, run_id: UUID) -> None:
        async with self._pool.connection() as connection:
            _ = await connection.execute(
                "DELETE FROM document_metadata_staging WHERE run_id = %s", (run_id,)
            )
            _ = await connection.execute(
                """UPDATE document_metadata_runs SET status = 'failed',
                   finished_at = now() WHERE id = %s AND status = 'running'""",
                (run_id,),
            )

    async def interrupt_metadata_runs(self) -> None:
        """Recover per-drive runs orphaned by a process crash at startup."""
        async with _staging_transaction(self._pool) as connection:
            _ = await connection.execute(
                """DELETE FROM document_metadata_staging AS staging
                   USING document_metadata_runs AS run
                   WHERE staging.run_id = run.id AND run.status = 'running'"""
            )
            _ = await connection.execute(
                """UPDATE document_metadata_runs SET status = 'interrupted',
                   finished_at = now() WHERE status = 'running'"""
            )
            _ = await connection.execute(
                """UPDATE operation_runs SET status = 'interrupted', finished_at = now(),
                   failure_code = 'process_interrupted',
                   summary = jsonb_set(
                     summary, '{drive_outcomes}',
                     COALESCE((
                       SELECT jsonb_object_agg(
                         outcome.key,
                         CASE WHEN outcome.value->>'status' = 'refreshing'
                           THEN jsonb_set(outcome.value, '{status}',
                             '\"interrupted\"'::jsonb, TRUE)
                           ELSE outcome.value END)
                       FROM jsonb_each(COALESCE(
                         operation_runs.summary->'drive_outcomes', '{}'::jsonb
                       )) AS outcome
                     ), '{}'::jsonb), TRUE)
                   WHERE kind = 'metadata.ingest' AND status = 'running'"""
            )
