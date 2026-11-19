"""Persist document metadata, observations, and inventory cursors."""

from dataclasses import asdict
from json import dumps
from typing import cast, override
from uuid import UUID, uuid4

from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import TypeAdapter

from domain.exceptions.metadata_ingest_errors import MetadataRunAlreadyActive
from domain.models.documents.document import Document, DocumentMetadata
from domain.models.documents.metadata_selection import MetadataSelection
from domain.ports.documents.document_store import (
    DocumentRefreshStore,
    DocumentStore,
    MetadataIngestRun,
)

_metadata_adapter = TypeAdapter(DocumentMetadata)


class PostgresDocumentStore(DocumentStore, DocumentRefreshStore):
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool: AsyncConnectionPool = pool

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
    async def start_metadata_run(
        self, principal_id: str, drive_id: str, selection: MetadataSelection
    ) -> MetadataIngestRun:
        run_id = uuid4()
        serialized_selection = dumps(asdict(selection), sort_keys=True)
        try:
            async with self._pool.connection() as connection:
                cursor = await connection.execute(
                    """SELECT selection, delta_link FROM document_metadata_cursors
                       WHERE principal_id = %s AND drive_id = %s""",
                    (principal_id, drive_id),
                )
                row = await cursor.fetchone()
                same_selection = (
                    row is not None and row[0] == serialized_selection
                )
                _ = await connection.execute(
                    """INSERT INTO document_metadata_runs
                       (id, principal_id, drive_id, selection, status)
                       VALUES (%s, %s, %s, %s, 'running')""",
                    (run_id, principal_id, drive_id, serialized_selection),
                )
        except UniqueViolation as exc:
            raise MetadataRunAlreadyActive(
                "A metadata run is already active for this operator and drive"
            ) from exc
        return MetadataIngestRun(
            run_id,
            row[1] if same_selection and row is not None else None,
            same_selection,
        )

    @override
    async def stage_document(self, run_id: UUID, document: Document) -> None:
        file = document.file_metadata
        async with self._pool.connection() as connection:
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
    async def stage_deleted(self, run_id: UUID, item_id: str) -> None:
        async with self._pool.connection() as connection:
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
        # The source records, observations, cursor, and run status commit together.
        async with self._pool.connection() as connection:
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
    async def fail_metadata_run(self, run_id: UUID) -> None:
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
        async with self._pool.connection() as connection:
            _ = await connection.execute(
                """DELETE FROM document_metadata_staging AS staging
                   USING document_metadata_runs AS run
                   WHERE staging.run_id = run.id AND run.status = 'running'"""
            )
            _ = await connection.execute(
                """UPDATE document_metadata_runs SET status = 'interrupted',
                   finished_at = now() WHERE status = 'running'"""
            )
