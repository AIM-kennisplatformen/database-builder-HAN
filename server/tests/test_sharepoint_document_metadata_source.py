import asyncio
from collections.abc import AsyncIterator
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from msgraph.graph_service_client import GraphServiceClient

from api.routes.documents.metadata import (
    InventoryMetadataRequestBody,
    inventory_metadata_endpoint,
)
from api.routes.documents.router import router as documents_router
from dependencies import get_document_metadata_source
from domain.exceptions.metadata_inventory_errors import MetadataSourceError
from domain.models.documents.content_version import ContentVersion
from domain.models.documents.document import (
    Document,
    DocumentId,
    DocumentMetadata,
    FileMetadata,
)
from domain.ports.sources.document_metadata_source import DocumentMetadataSource
from infrastructure.sources.sharepoint_document_metadata_source import (
    SharePointDocumentMetadataSource,
)


def test_stream_metadata_follows_pages_and_maps_matching_files() -> None:
    delta = MagicMock()
    delta.with_url.return_value.get = AsyncMock(
        side_effect=[
            Response(
                200,
                json={
                    "value": [
                        {"id": "folder", "name": "Folder", "folder": {}},
                        {
                            "id": "first",
                            "name": "Report.DOCX",
                            "cTag": "version-1",
                            "webUrl": "https://example.test/report",
                            "size": 12,
                            "file": {
                                "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                "hashes": {"sha1Hash": "abc"},
                            },
                        },
                        {"id": "other", "name": "Slides.pptx", "file": {}},
                    ],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/next",
                },
            ),
            Response(
                200,
                json={
                    "value": [
                        {"id": "deleted", "name": "Gone.docx", "deleted": {}},
                        {"id": "second", "name": "Notes.docx", "file": {}},
                    ],
                    "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta",
                },
            ),
        ]
    )
    client = MagicMock()
    client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value.delta = (
        delta
    )
    source = SharePointDocumentMetadataSource(cast(GraphServiceClient, client))

    async def collect():
        return [item async for item in source.stream_metadata("drive", ["docx"], None)]

    items = asyncio.run(collect())

    assert [document.id.id for document, _, _ in items] == [
        ("drive", "first"),
        ("drive", "second"),
    ]
    assert items[0][0].file_metadata.content_version.version == "version-1"
    assert items[0][0].file_metadata.hashes.sha1 == "abc"
    assert items[0][1:] == (1, 1)
    assert items[1][1:] == (2, 2)
    assert delta.with_url.call_count == 2


def test_graph_drive_error_is_reported_before_page_validation() -> None:
    delta = MagicMock()
    delta.with_url.return_value.get = AsyncMock(
        return_value=Response(
            400,
            json={
                "error": {
                    "code": "invalidRequest",
                    "message": "The supplied drive id does not represent a valid drive.",
                }
            },
        )
    )
    client = MagicMock()
    client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value.delta = (
        delta
    )
    source = SharePointDocumentMetadataSource(cast(GraphServiceClient, client))

    async def collect() -> None:
        async for _ in source.stream_metadata("bad-drive", ["pdf"], 10):
            pass

    with pytest.raises(MetadataSourceError) as error:
        asyncio.run(collect())

    assert error.value.status_code == 400
    assert "valid drive" in str(error.value)


def test_metadata_endpoint_returns_graph_client_error() -> None:
    class ErrorSource:
        def stream_metadata(
            self, drive_id: str, extensions: list[str], limit: int | None
        ):
            assert (drive_id, extensions, limit) == (
                "bad-drive",
                ["pdf"],
                10,
            )
            raise MetadataSourceError(400, "The supplied drive id is invalid")

    request = InventoryMetadataRequestBody(
        drive_id="bad-drive", extensions=["pdf"], limit=10
    )

    async def collect():
        return [
            event async for event in inventory_metadata_endpoint(request, ErrorSource())
        ]

    events = asyncio.run(collect())
    assert len(events) == 1
    assert events[0].event == "error"
    assert events[0].data["error_status"] == 400
    assert events[0].data["detail"] == "The supplied drive id is invalid"


def test_metadata_endpoint_streams_progress_and_completion() -> None:
    class OneDocumentSource:
        async def stream_metadata(
            self, drive_id: str, extensions: list[str], limit: int | None
        ) -> AsyncIterator[tuple[Document, int, int]]:
            assert (drive_id, extensions, limit) == ("drive", ["pdf"], 10)
            yield (
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
                1,
                1,
            )

    app = FastAPI()
    app.include_router(documents_router)

    async def get_source() -> DocumentMetadataSource:
        return OneDocumentSource()

    app.dependency_overrides[get_document_metadata_source] = get_source

    async def request() -> Response:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.post(
                "/api/v1/documents/metadata",
                json={"drive_id": "drive", "extensions": ["pdf"], "limit": 10},
            )

    response = asyncio.run(request())
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: progress" in response.text
    assert "event: complete" in response.text
    assert '"item_id": "item"' in response.text
