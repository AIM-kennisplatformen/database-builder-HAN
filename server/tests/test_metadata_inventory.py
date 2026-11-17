import asyncio
from collections.abc import AsyncIterator

import pytest

from domain.exceptions.metadata_inventory_errors import StoreUnavailable
from domain.models.documents.content_version import ContentVersion
from domain.models.documents.document import (
    Document,
    DocumentId,
    DocumentMetadata,
    FileMetadata,
)
from domain.ports.documents.document_metadata_source import DocumentMetadataItem
from domain.use_cases.metadata_inventory import (
    InventoryMetadataRequest,
    inventory_metadata,
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
        async def stream_metadata(
            self,
            drive_id: str,
            extensions: list[str],
            include_paths: list[str],
            exclude_paths: list[str],
        ) -> AsyncIterator[DocumentMetadataItem]:
            assert (drive_id, extensions, include_paths, exclude_paths) == (
                "drive",
                ["pdf"],
                [],
                [],
            )
            yield DocumentMetadataItem(document, 1, 1)

    class Store:
        async def save_document(self, document: Document) -> None:
            saved.append(document)

    async def collect() -> list[Document]:
        return [
            update.document
            async for update in inventory_metadata(
                InventoryMetadataRequest("drive", ["pdf"]), Source(), Store()
            )
            if saved == [document]
        ]

    assert asyncio.run(collect()) == [document]
    assert saved == [document]


def test_inventory_does_not_report_progress_when_save_fails() -> None:
    class Source:
        async def stream_metadata(
            self,
            drive_id: str,
            extensions: list[str],
            include_paths: list[str],
            exclude_paths: list[str],
        ) -> AsyncIterator[DocumentMetadataItem]:
            assert (drive_id, extensions, include_paths, exclude_paths) == (
                "drive",
                ["pdf"],
                [],
                [],
            )
            yield DocumentMetadataItem(
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
                1,
                1,
            )

    class BrokenStore:
        async def save_document(self, document: Document) -> None:
            assert document.id.id == ("drive", "item")
            raise RuntimeError("database unavailable")

    async def collect() -> list[Document]:
        return [
            update.document
            async for update in inventory_metadata(
                InventoryMetadataRequest("drive", ["pdf"]), Source(), BrokenStore()
            )
        ]

    with pytest.raises(StoreUnavailable, match="store is unavailable"):
        asyncio.run(collect())


def test_inventory_request_normalizes_optional_filters() -> None:
    request = InventoryMetadataRequest(
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
        InventoryMetadataRequest("drive", include_paths=[path])
