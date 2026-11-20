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
from domain.models.documents.metadata_selection import MetadataSelection
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
            extensions: list[str],
            include_paths: list[str],
            exclude_paths: list[str],
            exclude_extensions: list[str],
            root_item_id: str,
            cursor: str | None,
        ) -> DocumentMetadataScan:
            assert root_item_id == "root"
            assert (
                drive_id,
                extensions,
                include_paths,
                exclude_paths,
                exclude_extensions,
            ) == ("drive", ["pdf"], [], [], [])
            assert cursor is None
            return DocumentMetadataScan((document,), (), "next", False)

    class Store:
        async def begin_metadata_run(
            self, principal_id: str, drive_id: str, selection: MetadataSelection
        ) -> MetadataIngestRun:
            _ = (principal_id, drive_id, selection)
            return MetadataIngestRun(uuid4(), None, True)

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
            self, publications: tuple[MetadataRunPublication, ...]
        ) -> None:
            _ = publications

        async def discard_metadata_run(self, run_id: UUID) -> None:
            _ = run_id
            saved.clear()

    async def collect() -> list[Document]:
        return [
            update.document
            async for update in ingest_metadata(
                "operator",
                MetadataIngestDriveRequest("drive", ["pdf"]),
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
            extensions: list[str],
            include_paths: list[str],
            exclude_paths: list[str],
            exclude_extensions: list[str],
            root_item_id: str,
            cursor: str | None,
        ) -> DocumentMetadataScan:
            assert root_item_id == "root"
            assert (
                drive_id,
                extensions,
                include_paths,
                exclude_paths,
                exclude_extensions,
            ) == ("drive", ["pdf"], [], [], [])
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
            self, principal_id: str, drive_id: str, selection: MetadataSelection
        ) -> MetadataIngestRun:
            _ = (principal_id, drive_id, selection)
            return MetadataIngestRun(uuid4(), None, True)

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
            self, publications: tuple[MetadataRunPublication, ...]
        ) -> None:
            _ = publications

        async def discard_metadata_run(self, run_id: UUID) -> None:
            _ = run_id

    async def collect() -> list[Document]:
        return [
            update.document
            async for update in ingest_metadata(
                "operator",
                MetadataIngestDriveRequest("drive", ["pdf"]),
                Source(),
                BrokenStore(),
            )
        ]

    with pytest.raises(StoreUnavailable, match="store is unavailable"):
        asyncio.run(collect())


def test_inventory_request_normalizes_optional_filters() -> None:
    request = MetadataIngestDriveRequest(
        "drive",
        include_paths=["/Reports/2026/", "Reports/2026"],
        exclude_paths=["/Reports/2026/Drafts/"],
    )
    assert request.extensions == []
    assert request.include_paths == ["Reports/2026"]
    assert request.exclude_paths == ["Reports/2026/Drafts"]


@pytest.mark.parametrize("path", ["Reports//2026", "Reports/../2026", " ", "/"])
def test_inventory_request_rejects_invalid_folder_paths(path: str) -> None:
    with pytest.raises(ValueError, match="folder paths relative"):
        MetadataIngestDriveRequest("drive", include_paths=[path])
