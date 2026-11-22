"""Persist document metadata, observations, and inventory cursors."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import asdict
from json import dumps
from sys import exc_info
from typing import cast, override
from uuid import UUID, uuid4

import anyio
from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from domain.exceptions.metadata_ingest_errors import MetadataRunAlreadyActive
from domain.models.documents.document import Document, DocumentMetadata
from domain.models.documents.metadata_selection import MetadataSelection
from domain.ports.documents.document_store import (
    CombinedDocumentRefreshStore,
    DocumentStore,
    MetadataDriveIngestRun,
    MetadataDriveRunPublication,
)
from infrastructure.postgres.combined_operation_store import (
    CombinedOperationStoreMethods,
)
from infrastructure.postgres.document_ingestion_store import (
    DocumentIngestionStoreMethods,
)
from infrastructure.postgres.document_store_constants import METADATA_RESET_LOCK
from infrastructure.postgres.pool_store import PoolStorePostgres
from infrastructure.postgres.transactions import staging_transaction

_metadata_adapter = TypeAdapter(DocumentMetadata)


class DocumentStorePostgres(
    CombinedOperationStoreMethods,
    DocumentIngestionStoreMethods,
    PoolStorePostgres,
    DocumentStore,
    CombinedDocumentRefreshStore,
):
    """Persist document metadata runs and publish drive snapshots transactionally."""

    async def delete_all_metadata(self) -> bool:
        """Clear metadata and cursors unless a metadata operation is running.

        Returns:
            Whether the reset completed. ``False`` means an active metadata operation
            or drive run blocked the reset; ``True`` means it succeeded, including
            when the tables were already empty.
        """
        async with staging_transaction(self._pool) as connection:
            _ = await connection.execute(
                "SELECT pg_advisory_xact_lock(%s)", (METADATA_RESET_LOCK,)
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
        """Insert or replace one document's current source metadata."""
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
    ) -> MetadataDriveIngestRun:
        """Start a per-drive run and reuse a cursor only for the same selection.

        Args:
            principal_id: User whose visibility observations the run updates.
            drive_id: Drive being scanned.
            selection: Selection whose cursor may be reused.
            operation_id: Optional parent operation to associate with this run.

        Returns:
            The new run ID, reusable delta cursor if the selection matches, and
            whether it matched the previous cursor selection.

        Raises:
            MetadataRunAlreadyActive: If another run is active for this principal and
                drive.
        """
        run_id = uuid4()
        serialized_selection = dumps(asdict(selection), sort_keys=True)
        try:
            async with self._pool.connection() as connection:
                cursor = await connection.execute(
                    """SELECT cursors.selection, cursors.delta_link
                       FROM (SELECT pg_advisory_xact_lock(%s)) AS reset_lock
                       LEFT JOIN document_metadata_cursors AS cursors
                         ON cursors.principal_id = %s AND cursors.drive_id = %s""",
                    (METADATA_RESET_LOCK, principal_id, drive_id),
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
        return MetadataDriveIngestRun(
            run_id,
            delta_link,
            same_selection,
        )

    @override
    async def publish_metadata_drive(
        self,
        operation_id: UUID,
        drive_id: str,
        publication: MetadataDriveRunPublication,
        outcome: dict[str, object],
    ) -> None:
        """Publish one drive snapshot and its parent outcome atomically.

        Raises:
            ValueError: If the run is not active or does not belong to this operation
                and drive.
        """
        async with staging_transaction(self._pool) as connection:
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
            await self._apply_drive_publication(connection, publication)
            await self._set_drive_outcome(
                connection,
                operation_id,
                drive_id,
                outcome,
            )

    @override
    async def stage_document(self, run_id: UUID, document: Document) -> None:
        """Stage a visible document, replacing an earlier staged version of its item."""
        file = document.file_metadata
        async with staging_transaction(self._pool) as connection:
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
        """Stage an item as invisible in the next published snapshot."""
        async with staging_transaction(self._pool) as connection:
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
        """Atomically publish one drive run and advance its delta cursor.

        Args:
            run_id: Active staged run to publish.
            delta_link: Completed Graph delta cursor to store.
            baseline: Whether the scan covered the full drive scope.
            reconcile: Whether a baseline may hide previously observed but absent items.

        Raises:
            ValueError: If ``run_id`` is not active.
        """
        publication = MetadataDriveRunPublication(
            run_id, delta_link, baseline, reconcile
        )
        async with self._metadata_publication_transaction() as connection:
            await self._apply_drive_publication(connection, publication)

    @override
    async def publish_metadata_runs(
        self,
        publications: tuple[MetadataDriveRunPublication, ...],
        operation_id: UUID | None = None,
        summary: dict[str, object] | None = None,
        domain_ids: dict[str, object] | None = None,
    ) -> None:
        """Publish prepared drives and optionally complete their parent operation.

        All drive snapshots and the parent operation status commit in one transaction.
        An empty publication set is a no-op unless an operation ID is supplied.
        """
        if not publications and operation_id is None:
            return
        async with self._metadata_publication_transaction() as connection:
            for publication in publications:
                await self._apply_drive_publication(connection, publication)
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

    @asynccontextmanager
    async def _metadata_publication_transaction(
        self,
    ) -> AsyncGenerator[AsyncConnection[tuple[object, ...]]]:
        connection_context = self._pool.connection()
        connection = await connection_context.__aenter__()
        try:
            transaction_context = connection.transaction()
            _ = await transaction_context.__aenter__()
            try:
                yield connection
                # Deliver cancellation before entering the short commit boundary.
                await anyio.lowlevel.checkpoint()
            except BaseException:
                with anyio.CancelScope(shield=True):
                    _ = await transaction_context.__aexit__(*exc_info())
                raise
            else:
                with anyio.CancelScope(shield=True):
                    _ = await transaction_context.__aexit__(None, None, None)
        finally:
            with anyio.CancelScope(shield=True):
                _ = await connection_context.__aexit__(*exc_info())

    async def _apply_drive_publication(
        self,
        connection: AsyncConnection[tuple[object, ...]],
        publication: MetadataDriveRunPublication,
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
        """Remove staged rows and mark an active run failed without publishing it."""
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
        """Mark orphaned metadata runs and parent operations interrupted at startup."""
        async with staging_transaction(self._pool) as connection:
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
