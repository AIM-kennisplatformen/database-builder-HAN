"""Persist the latest metadata observed for each Graph drive item."""

from typing import override

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import TypeAdapter

from domain.models.documents.document import Document, DocumentMetadata
from domain.ports.documents.document_store import DocumentStore

_metadata_adapter = TypeAdapter(DocumentMetadata)


class PostgresDocumentStore(DocumentStore):
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
