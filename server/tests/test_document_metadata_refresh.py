import asyncio
from typing import cast, final, override
from uuid import UUID, uuid4

import pytest

from domain.exceptions.metadata_ingest_errors import SourceUnavailable
from domain.models.documents.content_version import ContentVersion
from domain.models.documents.document import (
    Document,
    DocumentId,
    DocumentMetadata,
    FileMetadata,
)
from domain.models.documents.metadata_selection import (
    MetadataPathRule,
    MetadataSelection,
)
from domain.ports.documents.document_metadata_source import (
    DocumentMetadataScan,
    DocumentRefreshSource,
)
from domain.ports.documents.document_store import (
    DocumentRefreshStore,
    MetadataIngestRun,
)
from domain.use_cases.metadata_ingest import MetadataIngestDriveRequest, ingest_metadata


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
        selection: MetadataSelection,
        root_item_id: str,
        cursor: str | None,
    ) -> DocumentMetadataScan:
        _ = (
            drive_id,
            selection,
            root_item_id,
        )
        self.received_cursor = cursor
        if isinstance(self.scan, Exception):
            raise self.scan
        return self.scan


class Store:
    def __init__(self, cursor: str | None = None) -> None:
        self.cursor: str | None = cursor
        self.staged: dict[str, Document] = {}
        self.deleted: set[str] = set()
        self.published: dict[str, Document] = {}
        self.failed: bool = False
        self.completed: bool = False
        self.selection: MetadataSelection | None = None

    async def begin_metadata_run(
        self,
        principal_id: str,
        drive_id: str,
        selection: MetadataSelection,
        operation_id: UUID | None = None,
    ) -> MetadataIngestRun:
        assert (principal_id, drive_id) == ("operator", "drive")
        self.selection = selection
        _ = operation_id
        return MetadataIngestRun(uuid4(), self.cursor, True)

    async def stage_document(self, run_id: object, document: Document) -> None:
        _ = run_id
        self.staged[document.id.id[1]] = document

    async def stage_deleted_item(self, run_id: object, item_id: str) -> None:
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

    async def discard_metadata_run(self, run_id: object) -> None:
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
            async for update in ingest_metadata(
                "operator",
                MetadataIngestDriveRequest("drive"),
                cast(DocumentRefreshSource, cast(object, source)),
                cast(DocumentRefreshStore, cast(object, store)),
            )
        ]

    assert asyncio.run(collect()) == ["new"]
    assert source.received_cursor == "old-cursor"
    assert store.selection == MetadataSelection((), ())
    assert store.published == {"new": new}
    assert store.cursor == "new-cursor"
    assert store.completed


def test_failed_scan_preserves_published_data_and_cursor() -> None:
    old = document("old")
    source = Source(RuntimeError("Graph failed"))
    store = Store("old-cursor")
    store.published["old"] = old

    async def collect() -> None:
        async for _ in ingest_metadata(
            "operator",
            MetadataIngestDriveRequest("drive"),
            cast(DocumentRefreshSource, cast(object, source)),
            cast(DocumentRefreshStore, cast(object, store)),
        ):
            pass

    with pytest.raises(SourceUnavailable):
        asyncio.run(collect())
    assert store.failed
    assert store.cursor == "old-cursor"
    assert store.published == {"old": old}


def test_cleanup_failure_is_logged_without_masking_scan_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    @final
    class FailingCleanupStore(Store):
        def __init__(self, cursor: str | None = None) -> None:
            super().__init__(cursor)
            self.cleanup_attempted: bool = False

        @override
        async def discard_metadata_run(self, run_id: object) -> None:
            _ = run_id
            self.cleanup_attempted = True
            raise RuntimeError("cleanup failed")

    old = document("old")
    store = FailingCleanupStore("old-cursor")
    store.published["old"] = old
    source = Source(RuntimeError("Graph failed"))

    async def collect() -> None:
        async for _ in ingest_metadata(
            "operator",
            MetadataIngestDriveRequest("drive"),
            cast(DocumentRefreshSource, cast(object, source)),
            cast(DocumentRefreshStore, cast(object, store)),
        ):
            pass

    with pytest.raises(SourceUnavailable, match="source is unavailable"):
        asyncio.run(collect())

    assert store.cleanup_attempted
    assert store.staged == {}
    assert store.published == {"old": old}
    assert store.cursor == "old-cursor"
    assert "Could not clear staging for metadata run" in caplog.text


def test_path_scope_uses_baseline_even_when_cursor_exists() -> None:
    source = Source(DocumentMetadataScan((), (), "new-cursor", True))
    store = Store("old-cursor")

    async def collect() -> None:
        async for _ in ingest_metadata(
            "operator",
            MetadataIngestDriveRequest(
                "drive",
                include_rules=(MetadataPathRule("Reports", ()),),
            ),
            cast(DocumentRefreshSource, cast(object, source)),
            cast(DocumentRefreshStore, cast(object, store)),
        ):
            pass

    asyncio.run(collect())
    assert source.received_cursor is None
    assert store.completed


def test_root_extension_filter_reuses_incremental_cursor() -> None:
    source = Source(DocumentMetadataScan((), (), "new-cursor", False))
    store = Store("old-cursor")

    async def collect() -> None:
        async for _ in ingest_metadata(
            "operator",
            MetadataIngestDriveRequest(
                "drive",
                include_rules=(MetadataPathRule("", (".PDF",)),),
            ),
            cast(DocumentRefreshSource, cast(object, source)),
            cast(DocumentRefreshStore, cast(object, store)),
        ):
            pass

    asyncio.run(collect())
    assert source.received_cursor == "old-cursor"
    assert store.selection is not None
    assert store.selection.include_rules == (MetadataPathRule("", ("pdf",)),)


def test_cancelled_refresh_discards_staging_and_preserves_published_catalog() -> None:
    @final
    class BlockingStore(Store):
        def __init__(self, cursor: str | None = None) -> None:
            super().__init__(cursor)
            self.staged_once = asyncio.Event()
            self.finish_stage = asyncio.Event()

        @override
        async def stage_document(self, run_id: object, document: Document) -> None:
            await super().stage_document(run_id, document)
            self.staged_once.set()
            await self.finish_stage.wait()

    async def exercise_cancellation() -> None:
        old = document("old")
        store = BlockingStore("old-cursor")
        store.published["old"] = old
        source = Source(
            DocumentMetadataScan((document("new"),), (), "new-cursor", False)
        )

        async def consume() -> None:
            async for _ in ingest_metadata(
                "operator",
                MetadataIngestDriveRequest("drive"),
                cast(DocumentRefreshSource, cast(object, source)),
                cast(DocumentRefreshStore, cast(object, store)),
            ):
                pass

        task = asyncio.create_task(consume())
        await asyncio.wait_for(store.staged_once.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert store.failed
        assert store.staged == {}
        assert store.published == {"old": old}
        assert store.cursor == "old-cursor"
        assert not store.completed

    asyncio.run(exercise_cancellation())
