import asyncio
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from domain.models.documents.content_version import ContentVersion
from domain.models.documents.document import (
    AuditActor,
    AuditEntry,
    Document,
    DocumentId,
    DocumentMetadata,
    FileHashes,
    FileMetadata,
)
from infrastructure.postgres.document_store import PostgresDocumentStore


def test_document_store_upserts_full_source_metadata() -> None:
    document = Document(
        _id=DocumentId("drive", "item"),
        file_metadata=FileMetadata(
            name="Report.pdf",
            mime_type="application/pdf",
            size_bytes=42,
            content_version=ContentVersion("v2"),
            source_url="https://example.test/report",
            hashes=FileHashes(sha1="sha1", sha256="sha256", quick_xor="xor"),
        ),
        document_metadata=DocumentMetadata(
            created=AuditEntry(
                timestamp=datetime(2026, 1, 1, 12, tzinfo=UTC),
                actor=AuditActor("Ada"),
            ),
            updated=AuditEntry(
                timestamp=datetime(2026, 3, 3, 12, tzinfo=UTC),
                actor=AuditActor("Bob"),
            ),
        ),
    )
    connection = MagicMock()
    connection.execute = AsyncMock()
    pool = MagicMock()
    pool.connection.return_value.__aenter__.return_value = connection

    asyncio.run(
        PostgresDocumentStore(cast(AsyncConnectionPool, pool)).save_document(document)
    )

    sql, values = connection.execute.call_args.args
    assert "ON CONFLICT (drive_id, item_id) DO UPDATE" in sql
    assert values[:10] == (
        "drive",
        "item",
        "Report.pdf",
        "application/pdf",
        42,
        "v2",
        "https://example.test/report",
        "sha1",
        "sha256",
        "xor",
    )
    assert isinstance(values[10], Jsonb)
    assert values[10].obj["created"]["actor"]["display_name"] == "Ada"
    assert values[10].obj["created"]["timestamp"] == "2026-01-01T12:00:00Z"
    assert values[10].obj["updated"]["actor"]["display_name"] == "Bob"
    assert values[10].obj["updated"]["timestamp"] == "2026-03-03T12:00:00Z"
    assert "authors" not in values[10].obj
    assert "update_history" not in values[10].obj
