import asyncio
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from httpx import Response
from msgraph.graph_service_client import GraphServiceClient
from sharepoint_document_metadata_test_support import selection

from infrastructure.msgraph.document_metadata_source import (
    SharePointDocumentMetadataSource,
)


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

    scan = asyncio.run(source.scan_metadata("drive", selection(), "root", old_cursor))

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
            selection(extensions=["pdf"]),
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
            selection(extensions=["pdf"]),
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

    scan = asyncio.run(source.scan_metadata("drive", selection(extensions=["pdf"])))

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
            "drive",
            selection(),
            "root",
            "https://graph.microsoft.com/v1.0/expired",
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
            item
            async for item in source.stream_metadata(
                "drive", selection(extensions=["docx"])
            )
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
