import asyncio
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import Response
from msgraph.graph_service_client import GraphServiceClient
from sharepoint_document_metadata_test_support import selection

from domain.ports.documents.document_metadata_source import (
    DocumentMetadataItem,
)
from infrastructure.msgraph.document_metadata.source import (
    DocumentMetadataSourceSharePoint,
)


def test_stream_metadata_exclusion_keeps_root_level_files() -> None:
    root = MagicMock()
    root.with_url.return_value.get = AsyncMock(
        side_effect=[
            Response(200, json={"id": "root", "folder": {}}),
            Response(200, json={"id": "drafts", "folder": {}}),
        ]
    )
    delta = MagicMock()
    delta.with_url.return_value.get = AsyncMock(
        return_value=Response(
            200,
            json={
                "value": [
                    {"id": "drafts", "folder": {}, "parentReference": {"id": "root"}},
                    {
                        "id": "keep",
                        "name": "Keep.pdf",
                        "file": {},
                        "parentReference": {"id": "root"},
                    },
                    {
                        "id": "skip",
                        "name": "Skip.pdf",
                        "file": {},
                        "parentReference": {"id": "drafts"},
                    },
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
    source = DocumentMetadataSourceSharePoint(cast(GraphServiceClient, client))

    async def collect() -> list[DocumentMetadataItem]:
        return [
            item
            async for item in source.stream_metadata(
                "drive", selection(exclude_paths=["Drafts"])
            )
        ]

    assert [item.document.id.id for item in asyncio.run(collect())] == [
        ("drive", "keep")
    ]


@pytest.mark.parametrize("folder_update", [None, {"id": "middle", "deleted": {}}])
def test_stream_metadata_rejects_incomplete_folder_ancestry(
    folder_update: dict[str, object] | None,
) -> None:
    root = MagicMock()
    root.with_url.return_value.get = AsyncMock(
        side_effect=[
            Response(200, json={"id": "root", "folder": {}}),
            Response(200, json={"id": "excluded", "folder": {}}),
        ]
    )
    delta = MagicMock()
    value: list[dict[str, object]] = [
        {"id": "excluded", "folder": {}, "parentReference": {"id": "root"}},
        {"id": "middle", "folder": {}, "parentReference": {"id": "excluded"}},
        {
            "id": "file",
            "name": "File.pdf",
            "file": {},
            "parentReference": {"id": "middle"},
        },
    ]
    if folder_update is None:
        value.pop(1)
    else:
        value.append(folder_update)
    delta.with_url.return_value.get = AsyncMock(
        return_value=Response(
            200,
            json={
                "value": value,
                "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta",
            },
        )
    )
    client = MagicMock()
    client.drives.by_drive_id.return_value.root = root
    client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value.delta = (
        delta
    )
    source = DocumentMetadataSourceSharePoint(cast(GraphServiceClient, client))

    async def collect() -> list[DocumentMetadataItem]:
        return [
            item
            async for item in source.stream_metadata(
                "drive", selection(exclude_paths=["Excluded"])
            )
        ]

    with pytest.raises(ValueError, match="incomplete folder ancestry"):
        asyncio.run(collect())


def test_stream_metadata_uses_latest_parent_for_repeated_folder() -> None:
    root = MagicMock()
    root.with_url.return_value.get = AsyncMock(
        side_effect=[
            Response(200, json={"id": "root", "folder": {}}),
            Response(200, json={"id": "excluded", "folder": {}}),
        ]
    )
    delta = MagicMock()
    delta.with_url.return_value.get = AsyncMock(
        return_value=Response(
            200,
            json={
                "value": [
                    {
                        "id": "excluded",
                        "name": "Excluded",
                        "folder": {},
                        "parentReference": {"id": "root"},
                    },
                    {
                        "id": "allowed",
                        "name": "Allowed",
                        "folder": {},
                        "parentReference": {"id": "root"},
                    },
                    {
                        "id": "moved",
                        "name": "Moved",
                        "folder": {},
                        "parentReference": {"id": "excluded"},
                    },
                    {
                        "id": "file",
                        "name": "File.pdf",
                        "file": {},
                        "parentReference": {"id": "moved"},
                    },
                    {
                        "id": "moved",
                        "name": "Moved",
                        "folder": {},
                        "parentReference": {"id": "allowed"},
                    },
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
    source = DocumentMetadataSourceSharePoint(cast(GraphServiceClient, client))

    async def collect() -> list[DocumentMetadataItem]:
        return [
            item
            async for item in source.stream_metadata(
                "drive", selection(exclude_paths=["Excluded"])
            )
        ]

    assert [item.document.id.id for item in asyncio.run(collect())] == [
        ("drive", "file")
    ]


def test_stream_metadata_scans_from_channel_folder_root() -> None:
    root = MagicMock()
    root.with_url.return_value.get = AsyncMock(
        return_value=Response(200, json={"id": "reports", "folder": {}})
    )
    delta = MagicMock()
    delta.with_url.return_value.get = AsyncMock(
        return_value=Response(
            200,
            json={
                "value": [
                    {
                        "id": "reports",
                        "name": "Reports",
                        "folder": {},
                        "parentReference": {"id": "channel-root"},
                    },
                    {
                        "id": "file",
                        "name": "Report.pdf",
                        "file": {},
                        "parentReference": {"id": "reports"},
                    },
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
    source = DocumentMetadataSourceSharePoint(cast(GraphServiceClient, client))

    async def collect() -> list[DocumentMetadataItem]:
        return [
            item
            async for item in source.stream_metadata(
                "drive", selection(include_paths=["Reports"]), "channel-root"
            )
        ]

    assert [item.document.id.id for item in asyncio.run(collect())] == [
        ("drive", "file")
    ]
    assert root.with_url.call_args.args[0] == (
        "https://graph.microsoft.com/v1.0/drives/drive/items/channel-root:/reports:"
    )
    assert delta.with_url.call_args.args[0] == (
        "https://graph.microsoft.com/v1.0/drives/drive/items/channel-root/delta"
    )
