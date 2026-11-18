import asyncio
from collections.abc import AsyncIterator
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from msgraph.graph_service_client import GraphServiceClient
from pydantic import ValidationError

from api.routes.documents.metadata import (
    DriveMetadataSelectorBody,
    InventoryMetadataRequestBody,
    inventory_metadata_endpoint,
)
from api.routes.documents.router import router as documents_router
from dependencies import (
    get_current_graph_user_id,
    get_document_metadata_source,
    get_document_store,
    get_group_drive_source,
)
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
from domain.ports.documents.group_drive_source import GroupDriveLocation, UserGroup
from infrastructure.msgraph.document_metadata_source import (
    SharePointDocumentMetadataSource,
)


@pytest.mark.parametrize(
    "removed_field",
    [{"limit": 10}, {"path": "Reports"}, {"include_drive_ids": ["drive"]}],
)
def test_metadata_request_rejects_removed_fields(
    removed_field: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        InventoryMetadataRequestBody.model_validate(removed_field)


def test_metadata_selector_requires_paths_list() -> None:
    with pytest.raises(ValidationError):
        InventoryMetadataRequestBody.model_validate(
            {"include": [{"drive_id": "drive", "paths": "Reports/"}]}
        )


class OneGroupSource:
    async def stream_user_groups(self, user_id: str) -> AsyncIterator[UserGroup]:
        assert user_id == "user"
        yield UserGroup("group", "Research")

    async def stream_group_drive_ids(self, group_id: str) -> AsyncIterator[str]:
        assert group_id == "group"
        yield "bad-drive"
        yield "drive"

    async def stream_group_drive_locations(
        self, group_id: str
    ) -> AsyncIterator[GroupDriveLocation]:
        async for drive_id in self.stream_group_drive_ids(group_id):
            yield GroupDriveLocation(drive_id)


def test_incremental_scan_uses_cursor_and_reports_deleted_items() -> None:
    delta = MagicMock()
    delta.with_url.return_value.get = AsyncMock(
        return_value=Response(
            200,
            json={
                "value": [
                    {"id": "removed", "deleted": {}},
                    {"id": "changed", "name": "Report.pdf", "file": {}},
                ],
                "@odata.deltaLink": "https://graph.microsoft.com/v1.0/new-cursor",
            },
        )
    )
    client = MagicMock()
    client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value.delta = (
        delta
    )
    source = SharePointDocumentMetadataSource(cast(GraphServiceClient, client))
    old_cursor = "https://graph.microsoft.com/v1.0/old-cursor"

    scan = asyncio.run(
        source.scan_metadata("drive", [], [], [], [], "root", old_cursor)
    )

    assert delta.with_url.call_args.args == (old_cursor,)
    assert [item.id.id[1] for item in scan.documents] == ["changed"]
    assert scan.deleted_item_ids == ("removed",)
    assert scan.delta_link.endswith("/new-cursor")
    assert not scan.baseline


def test_incremental_scan_marks_file_renamed_out_of_selected_extension_invisible() -> (
    None
):
    delta = MagicMock()
    delta.with_url.return_value.get = AsyncMock(
        return_value=Response(
            200,
            json={
                "value": [
                    {"id": "previously-visible", "name": "Report.txt", "file": {}}
                ],
                "@odata.deltaLink": "https://graph.microsoft.com/v1.0/new-cursor",
            },
        )
    )
    client = MagicMock()
    client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value.delta = (
        delta
    )
    source = SharePointDocumentMetadataSource(cast(GraphServiceClient, client))

    scan = asyncio.run(
        source.scan_metadata(
            "drive",
            ["pdf"],
            [],
            [],
            [],
            "root",
            "https://graph.microsoft.com/v1.0/old-cursor",
        )
    )

    assert scan.documents == ()
    assert scan.deleted_item_ids == ("previously-visible",)
    assert not scan.baseline


def test_incremental_scan_uses_last_matching_entry_for_same_file() -> None:
    delta = MagicMock()
    delta.with_url.return_value.get = AsyncMock(
        side_effect=[
            Response(
                200,
                json={
                    "value": [{"id": "item", "name": "Report.txt", "file": {}}],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/next",
                },
            ),
            Response(
                200,
                json={
                    "value": [{"id": "item", "name": "Report.pdf", "file": {}}],
                    "@odata.deltaLink": "https://graph.microsoft.com/v1.0/new-cursor",
                },
            ),
        ]
    )
    client = MagicMock()
    client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value.delta = (
        delta
    )
    source = SharePointDocumentMetadataSource(cast(GraphServiceClient, client))

    scan = asyncio.run(
        source.scan_metadata(
            "drive",
            ["pdf"],
            [],
            [],
            [],
            "root",
            "https://graph.microsoft.com/v1.0/old-cursor",
        )
    )

    assert [item.id.id[1] for item in scan.documents] == ["item"]
    assert scan.deleted_item_ids == ()
    assert delta.with_url.call_count == 2


def test_baseline_scan_does_not_mark_unselected_extension_invisible() -> None:
    delta = MagicMock()
    delta.with_url.return_value.get = AsyncMock(
        return_value=Response(
            200,
            json={
                "value": [{"id": "unselected", "name": "Report.txt", "file": {}}],
                "@odata.deltaLink": "https://graph.microsoft.com/v1.0/new-cursor",
            },
        )
    )
    client = MagicMock()
    client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value.delta = (
        delta
    )
    source = SharePointDocumentMetadataSource(cast(GraphServiceClient, client))

    scan = asyncio.run(source.scan_metadata("drive", ["pdf"], [], []))

    assert scan.baseline
    assert scan.documents == ()
    assert scan.deleted_item_ids == ()


def test_expired_cursor_restarts_full_baseline() -> None:
    delta = MagicMock()
    delta.with_url.return_value.get = AsyncMock(
        side_effect=[
            Response(410, json={"error": {"code": "resyncRequired"}}),
            Response(
                200,
                json={
                    "value": [{"id": "item", "name": "Report.pdf", "file": {}}],
                    "@odata.deltaLink": "https://graph.microsoft.com/v1.0/new-cursor",
                },
            ),
        ]
    )
    client = MagicMock()
    client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value.delta = (
        delta
    )
    source = SharePointDocumentMetadataSource(cast(GraphServiceClient, client))

    scan = asyncio.run(
        source.scan_metadata(
            "drive", [], [], [], [], "root", "https://graph.microsoft.com/v1.0/expired"
        )
    )

    assert scan.baseline
    assert [item.id.id[1] for item in scan.documents] == ["item"]
    assert [call.args[0] for call in delta.with_url.call_args_list] == [
        "https://graph.microsoft.com/v1.0/expired",
        "https://graph.microsoft.com/v1.0/drives/drive/root/delta",
    ]


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
        return [
            item async for item in source.stream_metadata("drive", ["docx"], [], [])
        ]

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
        side_effect=[
            Response(200, json={"id": "root", "folder": {}}),
            Response(200, json={"id": "folder-id", "folder": {}}),
        ]
    )
    delta = MagicMock()
    delta.with_url.return_value.get = AsyncMock(
        return_value=Response(
            200,
            json={
                "value": [
                    {
                        "id": "folder-id",
                        "name": "Reports",
                        "folder": {},
                        "parentReference": {"id": "root"},
                    },
                    {
                        "id": "first",
                        "name": "Report.pdf",
                        "file": {},
                        "parentReference": {"id": "folder-id"},
                    },
                    {
                        "id": "second",
                        "name": "Data.xlsx",
                        "file": {},
                        "parentReference": {"id": "folder-id"},
                    },
                    {
                        "id": "third",
                        "name": "README",
                        "file": {},
                        "parentReference": {"id": "folder-id"},
                    },
                    {
                        "id": "outside",
                        "name": "Other.pdf",
                        "file": {},
                        "parentReference": {"id": "root"},
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
    source = SharePointDocumentMetadataSource(cast(GraphServiceClient, client))

    async def collect() -> list[DocumentMetadataItem]:
        return [
            item
            async for item in source.stream_metadata(
                "drive", [], ["Team Reports/2026 #1"], []
            )
        ]

    items = asyncio.run(collect())
    assert [item.document.file_metadata.name for item in items] == [
        "Report.pdf",
        "Data.xlsx",
        "README",
    ]
    assert root.with_url.call_args_list[0].args[0] == (
        "https://graph.microsoft.com/v1.0/drives/drive/root"
    )
    assert root.with_url.call_args_list[1].args[0] == (
        "https://graph.microsoft.com/v1.0/drives/drive/"
        "root:/Team%20Reports/2026%20%231:"
    )
    assert client.drives.by_drive_id.return_value.items.by_drive_item_id.call_args_list[
        0
    ].args == ("root",)
    assert delta.with_url.call_args.args[0].endswith("/root/delta")


def test_stream_metadata_excludes_nested_folders_and_deduplicates_includes() -> None:
    root = MagicMock()
    root.with_url.return_value.get = AsyncMock(
        side_effect=[
            Response(200, json={"id": "root", "folder": {}}),
            Response(200, json={"id": "reports", "folder": {}}),
            Response(200, json={"id": "year", "folder": {}}),
            Response(200, json={"id": "drafts", "folder": {}}),
        ]
    )
    delta = MagicMock()
    delta.with_url.return_value.get = AsyncMock(
        return_value=Response(
            200,
            json={
                "value": [
                    {"id": "reports", "folder": {}, "parentReference": {"id": "root"}},
                    {"id": "year", "folder": {}, "parentReference": {"id": "reports"}},
                    {"id": "drafts", "folder": {}, "parentReference": {"id": "year"}},
                    {
                        "id": "one",
                        "name": "One.pdf",
                        "file": {},
                        "parentReference": {"id": "reports"},
                    },
                    {
                        "id": "two",
                        "name": "Two.pdf",
                        "file": {},
                        "parentReference": {"id": "year"},
                    },
                    {
                        "id": "skip",
                        "name": "Skip.pdf",
                        "file": {},
                        "parentReference": {"id": "drafts"},
                    },
                    {
                        "id": "outside",
                        "name": "Outside.pdf",
                        "file": {},
                        "parentReference": {"id": "root"},
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
    source = SharePointDocumentMetadataSource(cast(GraphServiceClient, client))

    async def collect() -> list[DocumentMetadataItem]:
        return [
            item
            async for item in source.stream_metadata(
                "drive", ["pdf"], ["Reports", "Reports/2026"], ["Reports/2026/Drafts"]
            )
        ]

    items = asyncio.run(collect())
    assert [item.document.id.id for item in items] == [
        ("drive", "one"),
        ("drive", "two"),
    ]
    assert all((item.total, item.available) == (2, 2) for item in items)
    assert delta.with_url.call_count == 1


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
    source = SharePointDocumentMetadataSource(cast(GraphServiceClient, client))

    async def collect() -> list[DocumentMetadataItem]:
        return [
            item async for item in source.stream_metadata("drive", [], [], ["Drafts"])
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
    source = SharePointDocumentMetadataSource(cast(GraphServiceClient, client))

    async def collect() -> list[DocumentMetadataItem]:
        return [
            item async for item in source.stream_metadata("drive", [], [], ["Excluded"])
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
                    {"id": "excluded", "folder": {}, "parentReference": {"id": "root"}},
                    {"id": "allowed", "folder": {}, "parentReference": {"id": "root"}},
                    {
                        "id": "moved",
                        "folder": {},
                        "parentReference": {"id": "excluded"},
                    },
                    {
                        "id": "file",
                        "name": "File.pdf",
                        "file": {},
                        "parentReference": {"id": "moved"},
                    },
                    {"id": "moved", "folder": {}, "parentReference": {"id": "allowed"}},
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
            item async for item in source.stream_metadata("drive", [], [], ["Excluded"])
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
    source = SharePointDocumentMetadataSource(cast(GraphServiceClient, client))

    async def collect() -> list[DocumentMetadataItem]:
        return [
            item
            async for item in source.stream_metadata(
                "drive", [], ["Reports"], [], [], "channel-root"
            )
        ]

    assert [item.document.id.id for item in asyncio.run(collect())] == [
        ("drive", "file")
    ]
    assert root.with_url.call_args.args[0] == (
        "https://graph.microsoft.com/v1.0/drives/drive/items/channel-root:/Reports:"
    )
    assert delta.with_url.call_args.args[0] == (
        "https://graph.microsoft.com/v1.0/drives/drive/items/channel-root/delta"
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
        return [item async for item in source.stream_metadata("drive", ["pdf"], [], [])]

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
            item async for item in source.stream_metadata("drive", [], [], [], ["docx"])
        ]

    items = asyncio.run(collect())
    assert [item.document.id.id for item in items] == [("drive", "keep")]
    assert (items[0].total, items[0].available) == (1, 1)


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
        return [item async for item in source.stream_metadata("drive", [], [], [])]

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
        return [item async for item in source.stream_metadata("drive", [], [], [])]

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
        async for _ in source.stream_metadata("bad-drive", ["pdf"], [], []):
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
            include_paths: list[str],
            exclude_paths: list[str],
            exclude_extensions: list[str],
            root_item_id: str,
        ):
            assert root_item_id == "root"
            assert (
                drive_id,
                extensions,
                include_paths,
                exclude_paths,
                exclude_extensions,
            ) == (
                "bad-drive",
                ["pdf"],
                [],
                [],
                [],
            )
            raise MetadataSourceError(400, "The supplied drive id is invalid")

    request = InventoryMetadataRequestBody(
        include=[DriveMetadataSelectorBody(drive_id="bad-drive", extensions=["pdf"])],
    )

    async def collect():
        return [
            event
            async for event in inventory_metadata_endpoint(
                "group", request, "user", OneGroupSource(), ErrorSource(), AsyncMock()
            )
        ]

    events = asyncio.run(collect())
    assert len(events) == 1
    assert events[0].event == "error"
    assert events[0].data["error_status"] == 400
    assert events[0].data["detail"] == "The supplied drive id is invalid"
    assert events[0].data["drive_id"] == "bad-drive"


def test_metadata_endpoint_skips_inaccessible_drive_and_completes() -> None:
    class PartlyAccessibleSource:
        async def stream_metadata(
            self,
            drive_id: str,
            extensions: list[str],
            include_paths: list[str],
            exclude_paths: list[str],
            exclude_extensions: list[str],
            root_item_id: str,
        ) -> AsyncIterator[DocumentMetadataItem]:
            assert root_item_id == "root"
            assert (extensions, include_paths, exclude_paths, exclude_extensions) == (
                [],
                [],
                [],
                [],
            )
            if drive_id == "bad-drive":
                raise MetadataSourceError(403, "Access denied")
            yield DocumentMetadataItem(
                Document(
                    _id=DocumentId(drive_id, "item"),
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

    async def collect():
        return [
            event
            async for event in inventory_metadata_endpoint(
                "group",
                InventoryMetadataRequestBody(),
                "user",
                OneGroupSource(),
                PartlyAccessibleSource(),
                AsyncMock(),
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == ["skipped", "progress", "complete"]
    assert events[0].data["drive_id"] == "bad-drive"
    assert events[0].data["error_status"] == 403
    assert events[-1].data["processed"] == 1
    assert events[-1].data["skipped_drive_ids"] == ["bad-drive"]


def test_metadata_endpoint_reports_explicitly_included_drive_denial() -> None:
    class DeniedSource:
        def stream_metadata(
            self,
            drive_id: str,
            extensions: list[str],
            include_paths: list[str],
            exclude_paths: list[str],
            exclude_extensions: list[str],
            root_item_id: str,
        ):
            assert drive_id == "bad-drive"
            assert (extensions, include_paths, exclude_paths, exclude_extensions) == (
                [],
                [],
                [],
                [],
            )
            assert root_item_id == "root"
            raise MetadataSourceError(403, "Access denied")

    async def collect():
        return [
            event
            async for event in inventory_metadata_endpoint(
                "group",
                InventoryMetadataRequestBody(
                    include=[DriveMetadataSelectorBody(drive_id="bad-drive")]
                ),
                "user",
                OneGroupSource(),
                DeniedSource(),
                AsyncMock(),
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == ["error"]
    assert events[0].data["drive_id"] == "bad-drive"
    assert events[0].data["error_status"] == 403


def test_metadata_endpoint_reports_explicit_drive_filtered_by_discovery() -> None:
    async def collect():
        return [
            event
            async for event in inventory_metadata_endpoint(
                "group",
                InventoryMetadataRequestBody(
                    include=[DriveMetadataSelectorBody(drive_id="hidden-drive")]
                ),
                "user",
                OneGroupSource(),
                AsyncMock(),
                AsyncMock(),
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == ["error"]
    assert events[0].data["drive_id"] == "hidden-drive"
    assert events[0].data["error_status"] == 403
    assert events[0].data["processed"] == 0


def test_metadata_endpoint_streams_progress_and_completion() -> None:
    class OneDocumentSource:
        async def stream_metadata(
            self,
            drive_id: str,
            extensions: list[str],
            include_paths: list[str],
            exclude_paths: list[str],
            exclude_extensions: list[str],
            root_item_id: str,
        ) -> AsyncIterator[DocumentMetadataItem]:
            assert root_item_id == "root"
            assert (
                drive_id,
                extensions,
                include_paths,
                exclude_paths,
                exclude_extensions,
            ) == (
                "drive",
                ["pdf"],
                [],
                [],
                [],
            )
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
    app.dependency_overrides[get_group_drive_source] = lambda: OneGroupSource()
    app.dependency_overrides[get_current_graph_user_id] = lambda: "user"

    async def request() -> Response:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.post(
                "/api/v1/documents/groups/group/metadata",
                json={
                    "include": [
                        {"drive_id": "drive", "paths": [], "extensions": ["pdf"]}
                    ],
                    "exclude": [],
                },
            )

    response = asyncio.run(request())
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: progress" in response.text
    assert "event: complete" in response.text
    assert '"item_id": "item"' in response.text
    assert response.text.count('"total": 1') == 1
    assert response.text.count('"available": 1') == 1
    assert [document.id.id for document in saved] == [("drive", "item")]


def test_metadata_endpoint_accepts_include_paths_without_extensions() -> None:
    class EmptySource:
        def __init__(self) -> None:
            self.items: list[DocumentMetadataItem] = []

        async def stream_metadata(
            self,
            drive_id: str,
            extensions: list[str],
            include_paths: list[str],
            exclude_paths: list[str],
            exclude_extensions: list[str],
            root_item_id: str,
        ) -> AsyncIterator[DocumentMetadataItem]:
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
                ["Reports/2026"],
                [],
                [],
            )
            for item in self.items:
                yield item

    request = InventoryMetadataRequestBody(
        include=[DriveMetadataSelectorBody(drive_id="drive", paths=["Reports/2026"])],
    )

    async def collect():
        return [
            event
            async for event in inventory_metadata_endpoint(
                "group", request, "user", OneGroupSource(), EmptySource(), AsyncMock()
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == ["complete"]
    assert events[0].data["processed"] == 0
