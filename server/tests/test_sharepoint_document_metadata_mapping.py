import asyncio
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import Response
from msgraph.graph_service_client import GraphServiceClient
from sharepoint_document_metadata_test_support import selection

from domain.exceptions.metadata_ingest_errors import (
    MetadataSourceError,
)
from domain.ports.documents.document_metadata_source import (
    DocumentMetadataItem,
)
from infrastructure.msgraph.document_metadata_source import (
    SharePointDocumentMetadataSource,
)


def test_stream_metadata_reports_total_after_repeated_and_deleted_items() -> None:
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
        return [
            item
            async for item in source.stream_metadata(
                "drive", selection(extensions=["pdf"])
            )
        ]

    items = asyncio.run(collect())
    assert [item.document.id.id for item in items] == [
        ("drive", "first"),
        ("drive", "third"),
    ]
    assert (items[0].total, items[0].available) == (2, 2)
    assert delta.with_url.call_count == 2
    item_builder.versions.with_url.assert_not_called()


def test_stream_metadata_excludes_file_extensions() -> None:
    delta = MagicMock()
    delta.with_url.return_value.get = AsyncMock(
        return_value=Response(
            200,
            json={
                "value": [
                    {"id": "keep", "name": "Keep.pdf", "file": {}},
                    {"id": "skip", "name": "Skip.docx", "file": {}},
                ],
                "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta",
            },
        )
    )
    client = MagicMock()
    client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value.delta = (
        delta
    )
    source = SharePointDocumentMetadataSource(cast(GraphServiceClient, client))

    async def collect() -> list[DocumentMetadataItem]:
        return [
            item
            async for item in source.stream_metadata(
                "drive", selection(exclude_extensions=["docx"])
            )
        ]

    items = asyncio.run(collect())
    assert [item.document.id.id for item in items] == [("drive", "keep")]
    assert (items[0].total, items[0].available) == (1, 1)


def test_extensionless_file_does_not_match_a_filter_for_its_basename() -> None:
    delta = MagicMock()
    delta.with_url.return_value.get = AsyncMock(
        return_value=Response(
            200,
            json={
                "value": [
                    {"id": "extensionless", "name": "README", "file": {}},
                    {"id": "readme-extension", "name": "file.readme", "file": {}},
                ],
                "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta",
            },
        )
    )
    client = MagicMock()
    client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value.delta = (
        delta
    )
    source = SharePointDocumentMetadataSource(cast(GraphServiceClient, client))

    async def collect() -> list[DocumentMetadataItem]:
        return [
            item
            async for item in source.stream_metadata(
                "drive", selection(exclude_extensions=["readme"])
            )
        ]

    items = asyncio.run(collect())
    assert [item.document.id.id[1] for item in items] == ["extensionless"]


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
        return [item async for item in source.stream_metadata("drive", selection())]

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
        return [item async for item in source.stream_metadata("drive", selection())]

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
        async for _ in source.stream_metadata(
            "bad-drive", selection(extensions=["pdf"])
        ):
            pass

    with pytest.raises(MetadataSourceError) as error:
        asyncio.run(collect())

    assert error.value.status_code == 400
    assert "valid drive" in str(error.value)
