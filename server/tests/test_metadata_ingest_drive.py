import asyncio
from uuid import UUID, uuid4

import pytest

from domain.exceptions.metadata_ingest_errors import StoreUnavailable
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
from domain.ports.documents.document_metadata_source import DocumentMetadataScan
from domain.ports.documents.document_store import (
    MetadataIngestRun,
    MetadataRunPublication,
)
from domain.use_cases.metadata_ingest import (
    MetadataIngestDriveRequest,
    ingest_metadata,
)


def test_inventory_saves_each_document_before_reporting_progress() -> None:
    document = Document(
        _id=DocumentId("drive", "item"),
        file_metadata=FileMetadata(
            name="Report.pdf",
            mime_type="application/pdf",
            size_bytes=10,
            content_version=ContentVersion("v1"),
            source_url="https://example.test/report",
        ),
        document_metadata=DocumentMetadata(),
    )
    saved: list[Document] = []

    class Source:
        async def scan_metadata(
            self,
            drive_id: str,
            selection: MetadataSelection,
            root_item_id: str,
            cursor: str | None,
            capture_relative_paths: bool = False,
        ) -> DocumentMetadataScan:
            _ = capture_relative_paths
            assert root_item_id == "root"
            assert drive_id == "drive"
            assert selection == MetadataSelection((MetadataPathRule("", ("pdf",)),), ())
            assert cursor is None
            return DocumentMetadataScan((document,), (), "next", False)

    class Store:
        async def begin_metadata_run(
            self,
            principal_id: str,
            drive_id: str,
            selection: MetadataSelection,
            operation_id: UUID | None = None,
        ) -> MetadataIngestRun:
            _ = (principal_id, drive_id, selection, operation_id)
            return MetadataIngestRun(uuid4(), None, True)

        async def begin_group_operation(self, principal_id: str, group_id: str) -> UUID:
            _ = (principal_id, group_id)
            return uuid4()

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
            publications: tuple[MetadataRunPublication, ...],
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

    async def collect() -> list[Document]:
        return [
            update.document
            async for update in ingest_metadata(
                "operator",
                MetadataIngestDriveRequest(
                    "drive",
                    include_rules=(MetadataPathRule("", ("pdf",)),),
                ),
                Source(),
                Store(),
            )
            if saved == [document]
        ]

    assert asyncio.run(collect()) == [document]
    assert saved == [document]


def test_inventory_does_not_report_progress_when_save_fails() -> None:
    class Source:
        async def scan_metadata(
            self,
            drive_id: str,
            selection: MetadataSelection,
            root_item_id: str,
            cursor: str | None,
            capture_relative_paths: bool = False,
        ) -> DocumentMetadataScan:
            _ = capture_relative_paths
            assert root_item_id == "root"
            assert drive_id == "drive"
            assert selection == MetadataSelection((MetadataPathRule("", ("pdf",)),), ())
            assert cursor is None
            return DocumentMetadataScan(
                (
                    Document(
                        _id=DocumentId("drive", "item"),
                        file_metadata=FileMetadata(
                            name="Report.pdf",
                            mime_type=None,
                            size_bytes=None,
                            content_version=ContentVersion(None),
                            source_url=None,
                        ),
                        document_metadata=DocumentMetadata(),
                    ),
                ),
                (),
                "next",
                False,
            )

    class BrokenStore:
        async def begin_metadata_run(
            self,
            principal_id: str,
            drive_id: str,
            selection: MetadataSelection,
            operation_id: UUID | None = None,
        ) -> MetadataIngestRun:
            _ = (principal_id, drive_id, selection, operation_id)
            return MetadataIngestRun(uuid4(), None, True)

        async def begin_group_operation(self, principal_id: str, group_id: str) -> UUID:
            _ = (principal_id, group_id)
            return uuid4()

        async def stage_document(self, run_id: UUID, document: Document) -> None:
            _ = run_id
            assert document.id.id == ("drive", "item")
            raise RuntimeError("database unavailable")

        async def stage_deleted_item(self, run_id: UUID, item_id: str) -> None:
            _ = (run_id, item_id)

        async def publish_metadata_run(
            self, run_id: UUID, delta_link: str, baseline: bool, reconcile: bool
        ) -> None:
            _ = (run_id, delta_link, baseline, reconcile)

        async def publish_metadata_runs(
            self,
            publications: tuple[MetadataRunPublication, ...],
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

    async def collect() -> list[Document]:
        return [
            update.document
            async for update in ingest_metadata(
                "operator",
                MetadataIngestDriveRequest(
                    "drive",
                    include_rules=(MetadataPathRule("", ("pdf",)),),
                ),
                Source(),
                BrokenStore(),
            )
        ]

    with pytest.raises(StoreUnavailable, match="store is unavailable"):
        asyncio.run(collect())


def test_inventory_request_normalizes_optional_filters() -> None:
    request = MetadataIngestDriveRequest(
        "drive",
        include_rules=(
            MetadataPathRule("/Reports/2026/", (".PDF", "md")),
            MetadataPathRule("reports\\2026", ("pdf", ".md")),
        ),
        exclude_rules=(MetadataPathRule("/Reports/2026/Drafts/", ()),),
    )
    assert request.include_rules == (MetadataPathRule("reports/2026", ("md", "pdf")),)
    assert request.exclude_rules == (MetadataPathRule("reports/2026/drafts", ()),)


@pytest.mark.parametrize("path", ["Reports//2026", "Reports/../2026", " ", "/", "///"])
def test_inventory_request_rejects_invalid_folder_paths(path: str) -> None:
    with pytest.raises(ValueError, match="folder paths relative"):
        MetadataIngestDriveRequest("drive", include_rules=(MetadataPathRule(path, ()),))


@pytest.mark.parametrize("extension", ["pdf/docx", "pdf*", "  "])
def test_inventory_request_rejects_invalid_rule_extensions(extension: str) -> None:
    with pytest.raises(ValueError, match="extensions must be alphanumeric"):
        MetadataIngestDriveRequest(
            "drive", include_rules=(MetadataPathRule("Reports", (extension,)),)
        )
