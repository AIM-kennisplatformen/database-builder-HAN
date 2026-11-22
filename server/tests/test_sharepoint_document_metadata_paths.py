import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from httpx import Response
from msgraph.graph_service_client import GraphServiceClient
from sharepoint_document_metadata_test_support import selection

from domain.models.documents.metadata_selection import (
    MetadataPathRule,
    MetadataSelection,
)
from domain.ports.documents.document_metadata_source import (
    DocumentMetadataItem,
)
from infrastructure.msgraph.document_metadata.source import (
    DocumentMetadataSourceSharePoint,
)


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
    source = DocumentMetadataSourceSharePoint(cast(GraphServiceClient, client))

    async def collect() -> list[DocumentMetadataItem]:
        return [
            item
            async for item in source.stream_metadata(
                "drive", selection(include_paths=["Reports"])
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
        "https://graph.microsoft.com/v1.0/drives/drive/root:/reports:"
    )


def test_selection_path_matches_graph_folder_name_case_insensitively() -> None:
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
                        "name": "Research",
                        "folder": {},
                        "parentReference": {"id": "root"},
                    },
                    {
                        "id": "file-id",
                        "name": "Report.pdf",
                        "file": {},
                        "parentReference": {"id": "folder-id"},
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
    selection = MetadataSelection(
        include_rules=(MetadataPathRule("research", ("pdf",)),),
        exclude_rules=(),
    )

    scan = asyncio.run(source.scan_metadata("drive", selection))

    assert [document.id.id[1] for document in scan.documents] == ["file-id"]
    assert client.drives.by_drive_id.return_value.items.by_drive_item_id.call_args_list[
        0
    ].args == ("root",)
    assert delta.with_url.call_args.args[0].endswith("/root/delta")


def test_path_scoped_scan_records_nested_relative_path_and_resolves_missing_folder_name() -> (
    None
):
    root = MagicMock()
    root.with_url.return_value.get = AsyncMock(
        side_effect=[
            Response(200, json={"id": "root", "folder": {}}),
            Response(200, json={"id": "research", "folder": {}}),
        ]
    )
    delta = MagicMock()
    delta.with_url.return_value.get = AsyncMock(
        return_value=Response(
            200,
            json={
                "value": [
                    {
                        "id": "research",
                        "name": "Research",
                        "folder": {},
                        "parentReference": {"id": "root"},
                    },
                    {
                        "id": "reports",
                        "folder": {},
                        "parentReference": {"id": "research"},
                    },
                    {
                        "id": "file-id",
                        "name": "Report.pdf",
                        "file": {},
                        "parentReference": {"id": "reports"},
                    },
                ],
                "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta",
            },
        )
    )
    drive = MagicMock()
    drive.root = root
    item_builder = drive.items.by_drive_item_id
    item_builder.return_value.delta = delta
    item_builder.return_value.get = AsyncMock(
        return_value=Response(
            200, json={"id": "reports", "name": "Reports", "folder": {}}
        )
    )
    client = MagicMock()
    client.drives.by_drive_id.return_value = drive
    source = DocumentMetadataSourceSharePoint(cast(GraphServiceClient, client))

    scan = asyncio.run(
        source.scan_metadata(
            "drive",
            MetadataSelection(
                include_rules=(MetadataPathRule("Research", ("pdf",)),),
                exclude_rules=(),
            ),
        )
    )

    assert len(scan.documents) == 1
    assert scan.documents[0].file_metadata.relative_path == "Research/Reports"
    item_builder.assert_any_call("reports")
    item_builder.return_value.get.assert_awaited_once()


def test_capture_relative_paths_keeps_broad_selection_and_records_folder_path() -> None:
    root = MagicMock()
    root.with_url.return_value.get = AsyncMock(
        return_value=Response(200, json={"id": "root", "folder": {}})
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
                        "parentReference": {"id": "root"},
                    },
                    {
                        "id": "file-id",
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

    scan = asyncio.run(
        source.scan_metadata(
            "drive",
            MetadataSelection(include_rules=(), exclude_rules=()),
            capture_relative_paths=True,
        )
    )

    assert [document.id.id[1] for document in scan.documents] == ["file-id"]
    assert scan.documents[0].file_metadata.relative_path == "Reports"
    assert scan.baseline is True
    assert root.with_url.call_args.args[0] == (
        "https://graph.microsoft.com/v1.0/drives/drive/root"
    )
    assert delta.with_url.call_args.args[0].endswith("/root/delta")


def test_stream_metadata_excludes_nested_folders_and_deduplicates_includes() -> None:
    root = MagicMock()
    folder_results: dict[str, dict[str, object]] = {
        "https://graph.microsoft.com/v1.0/drives/drive/root": {
            "id": "root",
            "folder": {},
        },
        "https://graph.microsoft.com/v1.0/drives/drive/root:/reports:": {
            "id": "reports",
            "folder": {},
        },
        "https://graph.microsoft.com/v1.0/drives/drive/root:/reports/2026:": {
            "id": "year",
            "folder": {},
        },
        "https://graph.microsoft.com/v1.0/drives/drive/root:/reports/2026/drafts:": {
            "id": "drafts",
            "folder": {},
        },
    }

    def folder_request(url: str) -> SimpleNamespace:
        return SimpleNamespace(
            get=AsyncMock(return_value=Response(200, json=folder_results[url]))
        )

    root.with_url.side_effect = folder_request
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
                        "parentReference": {"id": "root"},
                    },
                    {
                        "id": "year",
                        "name": "2026",
                        "folder": {},
                        "parentReference": {"id": "reports"},
                    },
                    {
                        "id": "drafts",
                        "name": "Drafts",
                        "folder": {},
                        "parentReference": {"id": "year"},
                    },
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
    source = DocumentMetadataSourceSharePoint(cast(GraphServiceClient, client))

    async def collect() -> list[DocumentMetadataItem]:
        return [
            item
            async for item in source.stream_metadata(
                "drive",
                selection(
                    extensions=["pdf"],
                    include_paths=["Reports", "Reports/2026"],
                    exclude_paths=["Reports/2026/Drafts"],
                ),
            )
        ]

    items = asyncio.run(collect())
    assert [item.document.id.id for item in items] == [
        ("drive", "one"),
        ("drive", "two"),
    ]
    assert all((item.total, item.available) == (2, 2) for item in items)
    assert delta.with_url.call_count == 1
