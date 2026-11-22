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
from domain.models.documents.document_ingestion import (
    DocumentIngestFileSelection,
    DocumentProcessingCandidate,
    DocumentProcessingState,
    ProcessingOutcome,
)
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
                    if same_selection and delta_link == "":
                        delta_link = None
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
                     run_id, item_id, visible, relative_path, name, mime_type, size_bytes,
                     content_version, source_url, sha1, sha256, quick_xor,
                     document_metadata
                   ) VALUES (%s, %s, TRUE, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (run_id, item_id) DO UPDATE SET
                     visible = TRUE, relative_path = EXCLUDED.relative_path,
                     name = EXCLUDED.name,
                     mime_type = EXCLUDED.mime_type, size_bytes = EXCLUDED.size_bytes,
                     content_version = EXCLUDED.content_version,
                     source_url = EXCLUDED.source_url, sha1 = EXCLUDED.sha1,
                     sha256 = EXCLUDED.sha256, quick_xor = EXCLUDED.quick_xor,
                     document_metadata = EXCLUDED.document_metadata""",
                (
                    run_id,
                    document.id.id[1],
                    file.relative_path,
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
               (principal_id, drive_id, item_id, selection, visible, observed_run_id,
                relative_path, name, content_version)
               SELECT %s, %s, item_id, %s, visible, %s, relative_path, name, content_version
               FROM document_metadata_staging WHERE run_id = %s
               ON CONFLICT (principal_id, drive_id, item_id) DO UPDATE SET
                 selection = EXCLUDED.selection,
                 visible = EXCLUDED.visible,
                 observed_run_id = EXCLUDED.observed_run_id,
                 relative_path = EXCLUDED.relative_path,
                 name = EXCLUDED.name,
                 content_version = EXCLUDED.content_version,
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

    @override
    async def query_visible_document_candidates(
        self, principal_id: str, drive_id: str, published_run_id: UUID
    ) -> tuple[DocumentProcessingCandidate, ...]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT observation.drive_id, observation.item_id, observation.name,
                          observation.relative_path, observation.content_version,
                          state.active_content_version, state.active_policy_version,
                          state.last_attempt_content_version,
                          state.last_attempt_policy_version, state.last_outcome,
                          state.drive_id,
                          COALESCE(state.deferred, FALSE)
                   FROM document_metadata_cursors AS cursor
                   JOIN document_metadata_runs AS run
                     ON run.id = cursor.published_run_id
                    AND run.principal_id = cursor.principal_id
                    AND run.drive_id = cursor.drive_id
                    AND run.selection = cursor.selection
                   JOIN document_metadata_observations AS observation
                     ON observation.principal_id = cursor.principal_id
                    AND observation.drive_id = cursor.drive_id
                    AND observation.selection = cursor.selection
                    AND observation.visible
                   JOIN document_ms_graph_sources AS source
                     ON source.drive_id = observation.drive_id
                    AND source.item_id = observation.item_id
                   LEFT JOIN document_processing_state AS state
                     ON state.drive_id = source.drive_id
                    AND state.item_id = source.item_id
                   WHERE cursor.principal_id = %s AND cursor.drive_id = %s
                     AND cursor.published_run_id = %s AND cursor.delta_link <> ''
                     AND run.status = 'complete'
                   ORDER BY observation.item_id""",
                (principal_id, drive_id, published_run_id),
            )
            rows = cast(list[tuple[object, ...]], await cursor.fetchall())
            if not rows:
                cursor = await connection.execute(
                    """SELECT EXISTS (
                           SELECT 1 FROM document_metadata_cursors AS cursor
                           JOIN document_metadata_runs AS run
                             ON run.id = cursor.published_run_id
                            AND run.principal_id = cursor.principal_id
                            AND run.drive_id = cursor.drive_id
                            AND run.selection = cursor.selection
                           WHERE cursor.principal_id = %s AND cursor.drive_id = %s
                             AND cursor.published_run_id = %s
                             AND cursor.delta_link <> '' AND run.status = 'complete'
                       )""",
                    (principal_id, drive_id, published_run_id),
                )
                validity_row = cast(tuple[bool] | None, await cursor.fetchone())
                if validity_row is None or not validity_row[0]:
                    raise ValueError(
                        "Published metadata snapshot is no longer current"
                    )
        candidates: list[DocumentProcessingCandidate] = []
        for row in rows:
            (
                row_drive,
                item_id,
                name,
                relative_path,
                content_version,
                active_content,
                active_policy,
                attempt_content,
                attempt_policy,
                outcome,
                state_drive_id,
                deferred,
            ) = row
            state = None
            if state_drive_id is not None:
                state = DocumentProcessingState(
                    cast(str | None, active_content),
                    cast(str | None, active_policy),
                    cast(str | None, attempt_content),
                    cast(str | None, attempt_policy),
                    ProcessingOutcome(cast(str, outcome)) if outcome else None,
                    bool(deferred),
                )
            candidates.append(
                DocumentProcessingCandidate(
                    cast(str, row_drive),
                    cast(str, item_id),
                    cast(str, name),
                    cast(str | None, relative_path),
                    cast(str | None, content_version),
                    state,
                )
            )
        return tuple(candidates)

    @override
    async def record_document_ingest_selections(
        self,
        operation_id: UUID,
        selections: tuple[DocumentIngestFileSelection, ...],
    ) -> None:
        async with _staging_transaction(self._pool) as connection:
            for selection in selections:
                _ = await connection.execute(
                    """INSERT INTO document_ingest_file_runs (
                         operation_id, drive_id, item_id, relative_path,
                         source_content_version, processing_policy_version,
                         statuses, selection_reasons, outcome
                       ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (operation_id, drive_id, item_id) DO UPDATE SET
                         relative_path = EXCLUDED.relative_path,
                         source_content_version = EXCLUDED.source_content_version,
                         processing_policy_version = EXCLUDED.processing_policy_version,
                         statuses = EXCLUDED.statuses,
                         selection_reasons = EXCLUDED.selection_reasons,
                         outcome = EXCLUDED.outcome, updated_at = now()""",
                    (
                        operation_id,
                        selection.drive_id,
                        selection.item_id,
                        selection.relative_path,
                        selection.source_content_version,
                        selection.processing_policy_version,
                        Jsonb([status.value for status in selection.statuses]),
                        Jsonb(list(selection.selection_reasons)),
                        selection.outcome,
                    ),
                )

    @override
    async def record_document_processing_outcome(
        self,
        drive_id: str,
        item_id: str,
        source_content_version: str | None,
        processing_policy_version: str,
        outcome: ProcessingOutcome | str,
        *,
        deferred: bool = False,
        failure_stage: str | None = None,
        failure_detail: str | None = None,
    ) -> None:
        outcome_value = (
            outcome.value if isinstance(outcome, ProcessingOutcome) else outcome
        )
        if outcome_value == "deferred":
            async with self._pool.connection() as connection:
                _ = await connection.execute(
                    """INSERT INTO document_processing_state
                         (drive_id, item_id, deferred, updated_at)
                       VALUES (%s, %s, TRUE, now())
                       ON CONFLICT (drive_id, item_id) DO UPDATE SET
                         deferred = TRUE, updated_at = now()""",
                    (drive_id, item_id),
                )
            return
        if outcome_value not in {item.value for item in ProcessingOutcome}:
            raise ValueError("Unsupported document processing outcome")
        active_update = outcome_value == ProcessingOutcome.SUCCEEDED.value
        async with self._pool.connection() as connection:
            _ = await connection.execute(
                """INSERT INTO document_processing_state (
                     drive_id, item_id, active_content_version, active_policy_version,
                     last_attempt_content_version, last_attempt_policy_version,
                     last_outcome, deferred, failure_stage, failure_detail, updated_at
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                   ON CONFLICT (drive_id, item_id) DO UPDATE SET
                     active_content_version = CASE WHEN %s THEN EXCLUDED.active_content_version
                       ELSE document_processing_state.active_content_version END,
                     active_policy_version = CASE WHEN %s THEN EXCLUDED.active_policy_version
                       ELSE document_processing_state.active_policy_version END,
                     last_attempt_content_version = EXCLUDED.last_attempt_content_version,
                     last_attempt_policy_version = EXCLUDED.last_attempt_policy_version,
                     last_outcome = EXCLUDED.last_outcome,
                     deferred = %s,
                     failure_stage = EXCLUDED.failure_stage,
                     failure_detail = EXCLUDED.failure_detail,
                     updated_at = now()""",
                (
                    drive_id,
                    item_id,
                    source_content_version if active_update else None,
                    processing_policy_version if active_update else None,
                    source_content_version,
                    processing_policy_version,
                    outcome_value,
                    deferred,
                    failure_stage,
                    failure_detail,
                    active_update,
                    active_update,
                    deferred,
                ),
            )
