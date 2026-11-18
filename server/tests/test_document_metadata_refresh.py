import asyncio
from typing import final
from uuid import uuid4

import pytest

from domain.exceptions.metadata_inventory_errors import SourceUnavailable
from domain.models.documents.content_version import ContentVersion
from domain.models.documents.document import (
    Document,
    DocumentId,
    DocumentMetadata,
    FileMetadata,
)
from domain.ports.documents.document_metadata_source import DocumentMetadataScan
from domain.ports.documents.document_store import DocumentMetadataRun
from domain.use_cases.metadata_inventory import (
    InventoryMetadataRequest,
    refresh_metadata,
)


def document(item_id: str = "item") -> Document:
    return Document(
        _id=DocumentId("drive", item_id),
        file_metadata=FileMetadata(
            name="Report.pdf",
            mime_type="application/pdf",
            size_bytes=10,
            content_version=ContentVersion("v1"),
            source_url=None,
        ),
        document_metadata=DocumentMetadata(),
    )


@final
class Source:
    def __init__(self, scan: DocumentMetadataScan | Exception) -> None:
        self.scan = scan
        self.received_cursor: str | None = None

    async def scan_metadata(
        self,
        drive_id: str,
        extensions: list[str],
        include_paths: list[str],
        exclude_paths: list[str],
        exclude_extensions: list[str],
        root_item_id: str,
        cursor: str | None,
    ) -> DocumentMetadataScan:
        _ = (
            drive_id,
            extensions,
            include_paths,
            exclude_paths,
            exclude_extensions,
            root_item_id,
        )
        self.received_cursor = cursor
        if isinstance(self.scan, Exception):
            raise self.scan
        return self.scan


@final
class Store:
    def __init__(self, cursor: str | None = None) -> None:
        self.cursor = cursor
        self.staged: dict[str, Document] = {}
        self.deleted: set[str] = set()
        self.published: dict[str, Document] = {}
        self.failed = False
        self.completed = False
        self.selection: str | None = None

    async def begin_metadata_run(
        self, principal_id: str, drive_id: str, selection: str
    ) -> DocumentMetadataRun:
        assert (principal_id, drive_id) == ("operator", "drive")
        self.selection = selection
        return DocumentMetadataRun(uuid4(), self.cursor, True)

    async def stage_document(self, run_id: object, document: Document) -> None:
        _ = run_id
        self.staged[document.id.id[1]] = document

    async def stage_deleted(self, run_id: object, item_id: str) -> None:
        _ = run_id
        self.deleted.add(item_id)

    async def publish_metadata_run(
        self, run_id: object, delta_link: str, baseline: bool, reconcile: bool
    ) -> None:
        _ = (run_id, baseline, reconcile)
        self.published.update(self.staged)
        for item_id in self.deleted:
            self.published.pop(item_id, None)
        self.cursor = delta_link
        self.completed = True

    async def fail_metadata_run(self, run_id: object) -> None:
        _ = run_id
        self.staged.clear()
        self.deleted.clear()
        self.failed = True


def test_refresh_publishes_documents_deletions_and_cursor_together() -> None:
    old = document("old")
    new = document("new")
    source = Source(DocumentMetadataScan((new,), ("old",), "new-cursor", False))
    store = Store("old-cursor")
    store.published["old"] = old

    async def collect() -> list[str]:
        return [
            update.document.id.id[1]
            async for update in refresh_metadata(
                "operator", InventoryMetadataRequest("drive"), source, store
            )
        ]

    assert asyncio.run(collect()) == ["new"]
    assert source.received_cursor == "old-cursor"
    assert store.published == {"new": new}
    assert store.cursor == "new-cursor"
    assert store.completed


def test_failed_scan_preserves_published_data_and_cursor() -> None:
    old = document("old")
    source = Source(RuntimeError("Graph failed"))
    store = Store("old-cursor")
    store.published["old"] = old

    async def collect() -> None:
        async for _ in refresh_metadata(
            "operator", InventoryMetadataRequest("drive"), source, store
        ):
            pass

    with pytest.raises(SourceUnavailable):
        asyncio.run(collect())
    assert store.failed
    assert store.cursor == "old-cursor"
    assert store.published == {"old": old}


def test_path_scope_uses_baseline_even_when_cursor_exists() -> None:
    source = Source(DocumentMetadataScan((), (), "new-cursor", True))
    store = Store("old-cursor")

    async def collect() -> None:
        async for _ in refresh_metadata(
            "operator",
            InventoryMetadataRequest("drive", include_paths=["Reports"]),
            source,
            store,
        ):
            pass

    asyncio.run(collect())
    assert source.received_cursor is None
    assert store.completed
