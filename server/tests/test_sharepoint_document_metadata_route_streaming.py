import asyncio
from typing import cast
from uuid import UUID, uuid4

from sharepoint_document_metadata_test_support import (
    MetadataRefreshStore,
    OneGroupSource,
    selection_arguments,
)

from api.routes.documents.metadata import (
    DriveMetadataSelectorBody,
    MetadataIngestRequestBody,
    ingest_group_metadata_endpoint,
)
from domain.models.documents.content_version import ContentVersion
from domain.models.documents.document import (
    Document,
    DocumentId,
    DocumentMetadata,
    FileMetadata,
)
from domain.models.documents.metadata_selection import (
    MetadataSelection,
)
from domain.ports.documents.document_metadata_source import (
    DocumentMetadataScan,
    DocumentRefreshSource,
)
from domain.ports.documents.document_store import (
    DocumentRefreshStore,
    MetadataDriveIngestRun,
    MetadataDriveRunPublication,
)


def test_metadata_endpoint_streams_progress_and_completion() -> None:
    class OneDocumentSource:
        async def scan_metadata(
            self,
            drive_id: str,
            selection: MetadataSelection,
            root_item_id: str,
            cursor: str | None,
            capture_relative_paths: bool = False,
        ) -> DocumentMetadataScan:
            _ = capture_relative_paths
            assert cursor is None
            assert root_item_id == "root"
            assert drive_id == "drive"
            assert selection.includes("A/report", "pdf")
            assert selection.includes("A/B/report", "md")
            assert selection.includes("A/B/report", "docx")
            assert not selection.includes("A/B/report", "pdf")
            assert not selection.includes("A/B/report", "xlsx")
            return DocumentMetadataScan(
                (
                    Document(
                        _id=DocumentId("drive", "item"),
                        file_metadata=FileMetadata(
                            name="Report.pdf",
                            mime_type="application/pdf",
                            size_bytes=10,
                            content_version=ContentVersion("v1"),
                            source_url=None,
                        ),
                        document_metadata=DocumentMetadata(),
                    ),
                ),
                (),
                "new-cursor",
                False,
            )

    saved: list[Document] = []

    class Store:
        async def begin_group_operation(self, principal_id: str, group_id: str) -> UUID:
            _ = (principal_id, group_id)
            return uuid4()

        async def begin_metadata_run(
            self,
            principal_id: str,
            drive_id: str,
            selection: MetadataSelection,
            operation_id: UUID | None = None,
        ) -> MetadataDriveIngestRun:
            _ = (principal_id, drive_id, selection, operation_id)
            return MetadataDriveIngestRun(uuid4(), None, True)

        async def stage_document(self, run_id: UUID, document: Document) -> None:
            _ = run_id
            saved.append(document)

        async def stage_deleted_item(self, run_id: UUID, item_id: str) -> None:
            _ = (run_id, item_id)

        async def publish_metadata_run(
            self, run_id: UUID, delta_link: str, baseline: bool, reconcile: bool
        ) -> None:
            _ = (run_id, delta_link, baseline, reconcile)

        async def publish_metadata_runs(
            self,
            publications: tuple[MetadataDriveRunPublication, ...],
            operation_id: UUID | None = None,
            summary: dict[str, object] | None = None,
            domain_ids: dict[str, object] | None = None,
        ) -> None:
            _ = (publications, operation_id, summary, domain_ids)

        async def fail_group_operation(
            self,
            operation_id: UUID,
            failure_code: str,
            *,
            interrupted: bool = False,
            summary: dict[str, object] | None = None,
        ) -> None:
            _ = (operation_id, failure_code, interrupted, summary)

        async def discard_metadata_run(self, run_id: UUID) -> None:
            _ = run_id
            saved.clear()

    async def collect():
        return [
            event
            async for event in ingest_group_metadata_endpoint(
                "group",
                MetadataIngestRequestBody(
                    include=[
                        DriveMetadataSelectorBody(
                            drive_id="drive",
                            paths=["/A/"],
                            extensions=[".PDF", ".md"],
                        ),
                        DriveMetadataSelectorBody(
                            drive_id="drive", paths=["A/B"], extensions=["docx"]
                        ),
                    ],
                    exclude=[
                        DriveMetadataSelectorBody(
                            drive_id="drive", paths=["A"], extensions=["xlsx"]
                        ),
                        DriveMetadataSelectorBody(
                            drive_id="drive", paths=["A/B"], extensions=["pdf"]
                        ),
                    ],
                ),
                "user",
                OneGroupSource(),
                OneDocumentSource(),
                Store(),
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == ["started", "progress", "complete"]
    operation_id = events[0].data["operation_id"]
    assert events[1].data["operation_id"] == operation_id
    assert events[1].data["item_id"] == "item"
    assert events[1].data["total"] == 1
    assert events[1].data["available"] == 1
    assert events[2].data["operation_id"] == operation_id
    assert [document.id.id for document in saved] == [("drive", "item")]


def test_metadata_endpoint_accepts_include_paths_without_extensions() -> None:
    class EmptySource:
        async def scan_metadata(
            self,
            drive_id: str,
            selection: MetadataSelection,
            root_item_id: str,
            cursor: str | None,
            capture_relative_paths: bool = False,
        ) -> DocumentMetadataScan:
            _ = capture_relative_paths
            extensions, include_paths, exclude_paths, exclude_extensions = (
                selection_arguments(selection)
            )
            assert cursor is None
            assert root_item_id == "root"
            assert (
                drive_id,
                extensions,
                include_paths,
                exclude_paths,
                exclude_extensions,
            ) == (
                "drive",
                [],
                ["reports/2026"],
                [],
                [],
            )
            return DocumentMetadataScan((), (), "new-cursor", True)

    request = MetadataIngestRequestBody(
        include=[DriveMetadataSelectorBody(drive_id="drive", paths=["Reports/2026"])],
    )

    async def collect():
        return [
            event
            async for event in ingest_group_metadata_endpoint(
                "group",
                request,
                "user",
                OneGroupSource(),
                EmptySource(),
                MetadataRefreshStore(),
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == ["started", "complete"]
    assert events[1].data["processed"] == 0


def test_metadata_endpoint_close_discards_staged_refresh() -> None:
    old_document = Document(
        _id=DocumentId("drive", "old"),
        file_metadata=FileMetadata(
            name="Old.pdf",
            mime_type="application/pdf",
            size_bytes=10,
            content_version=ContentVersion("v1"),
            source_url=None,
        ),
        document_metadata=DocumentMetadata(),
    )
    new_document = Document(
        _id=DocumentId("drive", "new"),
        file_metadata=FileMetadata(
            name="New.pdf",
            mime_type="application/pdf",
            size_bytes=10,
            content_version=ContentVersion("v2"),
            source_url=None,
        ),
        document_metadata=DocumentMetadata(),
    )

    class RefreshSource:
        async def scan_metadata(
            self,
            drive_id: str,
            selection: MetadataSelection,
            root_item_id: str,
            cursor: str | None,
            capture_relative_paths: bool = False,
        ) -> DocumentMetadataScan:
            _ = capture_relative_paths
            extensions, include_paths, exclude_paths, exclude_extensions = (
                selection_arguments(selection)
            )
            _ = (
                drive_id,
                extensions,
                include_paths,
                exclude_paths,
                exclude_extensions,
                root_item_id,
            )
            assert cursor == "old-cursor"
            return DocumentMetadataScan((new_document,), (), "new-cursor", False)

    class RefreshStore:
        def __init__(self) -> None:
            self.staged: dict[str, Document] = {}
            self.published: dict[str, Document] = {"old": old_document}
            self.cursor: str | None = "old-cursor"
            self.failed: bool = False
            self.operation_id: UUID | None = None

        async def begin_group_operation(self, principal_id: str, group_id: str) -> UUID:
            _ = (principal_id, group_id)
            self.operation_id = uuid4()
            return self.operation_id

        async def begin_metadata_run(
            self,
            principal_id: str,
            drive_id: str,
            selection: MetadataSelection,
            operation_id: UUID | None = None,
        ) -> MetadataDriveIngestRun:
            _ = (principal_id, drive_id, selection, operation_id)
            return MetadataDriveIngestRun(uuid4(), self.cursor, True)

        async def stage_document(self, run_id: object, document: Document) -> None:
            _ = run_id
            self.staged[document.id.id[1]] = document

        async def stage_deleted_item(self, run_id: object, item_id: str) -> None:
            _ = (run_id, item_id)

        async def publish_metadata_run(
            self, run_id: object, delta_link: str, baseline: bool, reconcile: bool
        ) -> None:
            _ = (run_id, delta_link, baseline, reconcile)
            self.published.update(self.staged)
            self.cursor = delta_link

        async def publish_metadata_runs(
            self,
            publications: tuple[MetadataDriveRunPublication, ...],
            operation_id: UUID | None = None,
            summary: dict[str, object] | None = None,
            domain_ids: dict[str, object] | None = None,
        ) -> None:
            assert operation_id == self.operation_id
            _ = (summary, domain_ids)
            self.published.update(self.staged)
            self.cursor = publications[-1].delta_link

        async def fail_group_operation(
            self,
            operation_id: UUID,
            failure_code: str,
            *,
            interrupted: bool = False,
            summary: dict[str, object] | None = None,
        ) -> None:
            assert operation_id == self.operation_id
            _ = (failure_code, interrupted, summary)

        async def discard_metadata_run(self, run_id: object) -> None:
            _ = run_id
            self.staged.clear()
            self.failed = True

    async def close_after_first_progress() -> None:
        store = RefreshStore()
        endpoint = ingest_group_metadata_endpoint(
            "group",
            MetadataIngestRequestBody(),
            "user",
            OneGroupSource(),
            cast(DocumentRefreshSource, cast(object, RefreshSource())),
            cast(DocumentRefreshStore, cast(object, store)),
        )

        started = await anext(endpoint)
        assert started.event == "started"
        event = await anext(endpoint)
        assert event.event == "progress"
        assert store.staged == {"new": new_document}
        await endpoint.aclose()

        assert store.failed
        assert store.staged == {}
        assert store.published == {"old": old_document}
        assert store.cursor == "old-cursor"

    asyncio.run(close_after_first_progress())
