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
from dependencies import get_document_metadata_source, get_document_store
from domain.exceptions.metadata_inventory_errors import MetadataSourceError
from domain.models.documents.content_version import ContentVersion
from domain.models.documents.document import (
    Document,
    DocumentId,
    DocumentMetadata,
    FileMetadata,
)
from domain.ports.documents.document_metadata_source import (
    DocumentMetadataItem,
    DocumentMetadataSource,
)
from domain.ports.documents.document_store import DocumentStore
from infrastructure.msgraph.document_metadata_source import (
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

    assert [item.document.id.id for item in items] == [
        ("drive", "first"),
        ("drive", "second"),
    ]
    assert items[0].document.file_metadata.content_version.version == "version-1"
    assert items[0].document.file_metadata.hashes.sha1 == "abc"
    assert (items[0].total, items[0].available) == (2, 2)
    assert (items[1].total, items[1].available) == (2, 2)
    assert delta.with_url.call_count == 2


def test_stream_metadata_scopes_to_folder_and_accepts_all_file_types() -> None:
    root = MagicMock()
    root.with_url.return_value.get = AsyncMock(
        return_value=Response(200, json={"id": "folder-id", "folder": {}})
    )
    delta = MagicMock()
    delta.with_url.return_value.get = AsyncMock(
        return_value=Response(
            200,
            json={
                "value": [
                    {"id": "folder-id", "name": "Reports", "folder": {}},
                    {"id": "first", "name": "Report.pdf", "file": {}},
                    {"id": "second", "name": "Data.xlsx", "file": {}},
                    {"id": "third", "name": "README", "file": {}},
                ],
                "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta",
            },
        )
    )
    client = MagicMock()
    client.drives.by_drive_id.return_value.root = root
    client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value.delta = (
        delta
    )
    source = SharePointDocumentMetadataSource(cast(GraphServiceClient, client))

    async def collect() -> list[DocumentMetadataItem]:
        return [
            item
            async for item in source.stream_metadata(
                "drive", [], None, "Team Reports/2026 #1"
            )
        ]

    items = asyncio.run(collect())
    assert [item.document.file_metadata.name for item in items] == [
        "Report.pdf",
        "Data.xlsx",
        "README",
    ]
    assert root.with_url.call_args.args[0] == (
        "https://graph.microsoft.com/v1.0/drives/drive/"
        "root:/Team%20Reports/2026%20%231:"
    )
    assert client.drives.by_drive_id.return_value.items.by_drive_item_id.call_args_list[
        0
    ].args == ("folder-id",)
    assert delta.with_url.call_args.args[0].endswith("/items/folder-id/delta")


def test_stream_metadata_reports_available_and_limited_total() -> None:
    delta = MagicMock()
    delta.with_url.return_value.get = AsyncMock(
        side_effect=[
            Response(
                200,
                json={
                    "value": [
                        {"id": "first", "name": "One.pdf", "file": {}},
                        {"id": "second", "name": "Two.pdf", "file": {}},
                    ],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/next",
                },
            ),
            Response(
                200,
                json={
                    "value": [
                        {"id": "third", "name": "Three.pdf", "file": {}},
                        {"id": "second", "name": "Two.pdf", "deleted": {}},
                    ],
                    "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta",
                },
            ),
        ]
    )
    client = MagicMock()
    item_builder = (
        client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value
    )
    item_builder.delta = delta
    source = SharePointDocumentMetadataSource(cast(GraphServiceClient, client))

    async def collect() -> list[DocumentMetadataItem]:
        return [item async for item in source.stream_metadata("drive", ["pdf"], 1)]

    items = asyncio.run(collect())
    assert [item.document.id.id for item in items] == [("drive", "first")]
    assert (items[0].total, items[0].available) == (1, 2)
    assert delta.with_url.call_count == 2
    item_builder.versions.with_url.assert_not_called()


def test_stream_metadata_maps_creation_and_latest_update_without_version_request() -> (
    None
):
    delta = MagicMock()
    delta.with_url.return_value.get = AsyncMock(
        return_value=Response(
            200,
            json={
                "value": [
                    {
                        "id": "item",
                        "name": "Report.docx",
                        "file": {},
                        "createdDateTime": "2026-01-01T12:00:00Z",
                        "createdBy": {"user": {"displayName": "Ada"}},
                        "lastModifiedDateTime": "2026-03-03T12:00:00Z",
                        "lastModifiedBy": {
                            "application": {"displayName": "Migration tool"}
                        },
                    }
                ],
                "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta",
            },
        )
    )
    client = MagicMock()
    item_builder = (
        client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value
    )
    item_builder.delta = delta
    source = SharePointDocumentMetadataSource(cast(GraphServiceClient, client))

    async def collect() -> list[DocumentMetadataItem]:
        return [item async for item in source.stream_metadata("drive", [], None)]

    document = asyncio.run(collect())[0].document
    created = document.document_metadata.created
    updated = document.document_metadata.updated
    assert created is not None
    assert created.timestamp.isoformat() == "2026-01-01T12:00:00+00:00"
    assert created.actor is not None
    assert created.actor.display_name == "Ada"
    assert updated is not None
    assert updated.timestamp.isoformat() == "2026-03-03T12:00:00+00:00"
    assert updated.actor is not None
    assert updated.actor.display_name == "Migration tool"
    item_builder.versions.with_url.assert_not_called()


def test_stream_metadata_keeps_audit_timestamps_when_actors_are_missing() -> None:
    delta = MagicMock()
    delta.with_url.return_value.get = AsyncMock(
        return_value=Response(
            200,
            json={
                "value": [
                    {
                        "id": "item",
                        "name": "Report.docx",
                        "file": {},
                        "createdDateTime": "2026-01-01T12:00:00Z",
                        "lastModifiedDateTime": "2026-01-01T12:00:00Z",
                    }
                ],
                "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta",
            },
        )
    )
    client = MagicMock()
    item_builder = (
        client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value
    )
    item_builder.delta = delta
    source = SharePointDocumentMetadataSource(cast(GraphServiceClient, client))

    async def collect() -> list[DocumentMetadataItem]:
        return [item async for item in source.stream_metadata("drive", [], None)]

    document = asyncio.run(collect())[0].document
    assert document.document_metadata.created is not None
    assert document.document_metadata.created.actor is None
    assert document.document_metadata.updated is not None
    assert document.document_metadata.updated.actor is None


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
            self,
            drive_id: str,
            extensions: list[str],
            limit: int | None,
            path: str | None = None,
        ):
            assert (drive_id, extensions, limit, path) == (
                "bad-drive",
                ["pdf"],
                10,
                None,
            )
            raise MetadataSourceError(400, "The supplied drive id is invalid")

    request = InventoryMetadataRequestBody(
        drive_id="bad-drive", extensions=["pdf"], limit=10
    )

    async def collect():
        return [
            event
            async for event in inventory_metadata_endpoint(
                request, ErrorSource(), AsyncMock()
            )
        ]

    events = asyncio.run(collect())
    assert len(events) == 1
    assert events[0].event == "error"
    assert events[0].data["error_status"] == 400
    assert events[0].data["detail"] == "The supplied drive id is invalid"


def test_metadata_endpoint_streams_progress_and_completion() -> None:
    class OneDocumentSource:
        async def stream_metadata(
            self,
            drive_id: str,
            extensions: list[str],
            limit: int | None,
            path: str | None = None,
        ) -> AsyncIterator[DocumentMetadataItem]:
            assert (drive_id, extensions, limit, path) == ("drive", ["pdf"], 10, None)
            yield DocumentMetadataItem(
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
    saved: list[Document] = []

    async def get_source() -> DocumentMetadataSource:
        return OneDocumentSource()

    class Store:
        async def save_document(self, document: Document) -> None:
            saved.append(document)

    async def get_store() -> DocumentStore:
        return Store()

    app.dependency_overrides[get_document_metadata_source] = get_source
    app.dependency_overrides[get_document_store] = get_store

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
    assert response.text.count('"total": 1') == 2
    assert response.text.count('"available": 1') == 2
    assert [document.id.id for document in saved] == [("drive", "item")]


def test_metadata_endpoint_accepts_path_without_extensions() -> None:
    class EmptySource:
        def __init__(self) -> None:
            self.items: list[DocumentMetadataItem] = []

        async def stream_metadata(
            self,
            drive_id: str,
            extensions: list[str],
            limit: int | None,
            path: str | None = None,
        ) -> AsyncIterator[DocumentMetadataItem]:
            assert (drive_id, extensions, limit, path) == (
                "drive",
                [],
                None,
                "Reports/2026",
            )
            for item in self.items:
                yield item

    request = InventoryMetadataRequestBody(drive_id="drive", path="Reports/2026")

    async def collect():
        return [
            event
            async for event in inventory_metadata_endpoint(
                request, EmptySource(), AsyncMock()
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == ["complete"]
    assert events[0].data["total"] == 0
    assert events[0].data["available"] == 0
