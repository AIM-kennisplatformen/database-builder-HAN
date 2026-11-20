import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import cast, override
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from httpx import Response
from msgraph.graph_service_client import GraphServiceClient
from pydantic import ValidationError

from api.routes.documents import metadata as metadata_route
from api.routes.documents.metadata import (
    DriveMetadataSelectorBody,
    MetadataIngestRequestBody,
    ingest_group_metadata_endpoint,
)
from domain.exceptions.metadata_ingest_errors import (
    MetadataRunAlreadyActive,
    MetadataSourceError,
)
from domain.models.documents.content_version import ContentVersion
from domain.models.documents.document import (
    Document,
    DocumentId,
    DocumentMetadata,
    FileMetadata,
)
from domain.models.documents.metadata_selection import MetadataSelection
from domain.ports.documents.document_metadata_source import (
    DocumentMetadataItem,
    DocumentMetadataScan,
    DocumentRefreshSource,
)
from domain.ports.documents.document_store import (
    DocumentRefreshStore,
    MetadataIngestRun,
    MetadataRunPublication,
)
from domain.ports.documents.group_drive_source import (
    GroupDriveLocation,
    GroupDriveSource,
    UserGroup,
)
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
        MetadataIngestRequestBody.model_validate(removed_field)


def test_metadata_selector_requires_paths_list() -> None:
    with pytest.raises(ValidationError):
        MetadataIngestRequestBody.model_validate(
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


class MetadataRefreshStore:
    def __init__(self) -> None:
        self.cursor: str | None = None
        self.staged: list[Document] = []
        self.published: list[Document] = []
        self.operation_id: UUID | None = None
        self.operation_status: str | None = None

    async def begin_group_operation(self, principal_id: str, group_id: str) -> UUID:
        _ = (principal_id, group_id)
        self.operation_id = uuid4()
        self.operation_status = "running"
        return self.operation_id

    async def begin_metadata_run(
        self,
        principal_id: str,
        drive_id: str,
        selection: MetadataSelection,
        operation_id: UUID | None = None,
    ) -> MetadataIngestRun:
        _ = (principal_id, drive_id, selection, operation_id)
        return MetadataIngestRun(uuid4(), self.cursor, True)

    async def stage_document(self, run_id: UUID, document: Document) -> None:
        _ = run_id
        self.staged.append(document)

    async def stage_deleted_item(self, run_id: UUID, item_id: str) -> None:
        _ = (run_id, item_id)

    async def publish_metadata_run(
        self, run_id: UUID, delta_link: str, baseline: bool, reconcile: bool
    ) -> None:
        _ = (run_id, baseline, reconcile)
        self.published.extend(self.staged)
        self.staged.clear()
        self.cursor = delta_link

    async def publish_metadata_runs(
        self,
        publications: tuple[MetadataRunPublication, ...],
        operation_id: UUID | None = None,
        summary: dict[str, object] | None = None,
        domain_ids: dict[str, object] | None = None,
    ) -> None:
        assert operation_id == self.operation_id
        _ = (summary, domain_ids)
        self.operation_status = "complete"
        self.published.extend(self.staged)
        self.staged.clear()
        if publications:
            self.cursor = publications[-1].delta_link

    async def fail_group_operation(
        self,
        operation_id: UUID,
        failure_code: str,
        *,
        interrupted: bool = False,
        summary: dict[str, object] | None = None,
    ) -> None:
        assert operation_id == self.operation_id
        _ = (failure_code, summary)
        self.operation_status = "interrupted" if interrupted else "failed"

    async def discard_metadata_run(self, run_id: UUID) -> None:
        _ = run_id
        self.staged.clear()


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


@pytest.mark.parametrize(
    ("graph_status", "event_status"),
    [(403, 403), (404, 404), (429, 503), (400, 502), (500, 502)],
)
def test_metadata_endpoint_returns_graph_client_error(
    graph_status: int,
    event_status: int,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setattr(
        metadata_route,
        "config",
        SimpleNamespace(stream_progress_interval_seconds=1),
    )

    class ErrorSource:
        def __init__(self) -> None:
            self.status_code: int = graph_status

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
            assert cursor is None
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
            raise MetadataSourceError(
                self.status_code, "The supplied drive id is invalid"
            )

    request = MetadataIngestRequestBody(
        include=[DriveMetadataSelectorBody(drive_id="bad-drive", extensions=["pdf"])],
    )

    async def collect():
        return [
            event
            async for event in ingest_group_metadata_endpoint(
                "group",
                request,
                "user",
                OneGroupSource(),
                ErrorSource(),
                MetadataRefreshStore(),
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == ["started", "error"]
    operation_id = events[0].data["operation_id"]
    assert events[1].data == {
        "status": "error",
        "operation_id": operation_id,
        "error": {
            "code": "metadata.source_error",
            "http_status": event_status,
            "detail": "Metadata source request failed",
        },
        "context": {
            "operation": "metadata.ingest",
            "group_id": "group",
            "drive_id": "bad-drive",
            "operation_id": operation_id,
        },
        "progress": {"processed": 0},
    }
    expected_logs = 1 if event_status >= 500 else 0
    api_failure_records = [
        record for record in caplog.records if record.name == "api.errors"
    ]
    assert len(api_failure_records) == expected_logs


def test_metadata_endpoint_failure_reports_group_progress_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        metadata_route,
        "config",
        SimpleNamespace(stream_progress_interval_seconds=1),
    )

    class DriveThenFailureGroup(OneGroupSource):
        @override
        async def stream_group_drive_ids(self, group_id: str) -> AsyncIterator[str]:
            assert group_id == "group"
            yield "drive"
            yield "bad-drive"

    class FailureAfterProgressSource:
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
            _ = (
                extensions,
                include_paths,
                exclude_paths,
                exclude_extensions,
                root_item_id,
                cursor,
            )
            if drive_id == "bad-drive":
                raise MetadataSourceError(503, "Graph unavailable")
            return DocumentMetadataScan(
                (
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
                ),
                (),
                "new-cursor",
                False,
            )

    async def collect():
        return [
            event
            async for event in ingest_group_metadata_endpoint(
                "group",
                MetadataIngestRequestBody(),
                "user",
                DriveThenFailureGroup(),
                FailureAfterProgressSource(),
                MetadataRefreshStore(),
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == ["started", "progress", "error"]
    operation_id = events[0].data["operation_id"]
    assert events[1].data["processed"] == 1
    assert events[2].data == {
        "status": "error",
        "operation_id": operation_id,
        "error": {
            "code": "metadata.source_error",
            "http_status": 502,
            "detail": "Metadata source request failed",
        },
        "context": {
            "operation": "metadata.ingest",
            "group_id": "group",
            "drive_id": "bad-drive",
            "operation_id": operation_id,
        },
        "progress": {"processed": 1},
    }


def test_metadata_known_error_subclass_inherits_status_and_hides_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEBUG", "true")

    class SpecializedRunAlreadyActive(MetadataRunAlreadyActive):
        pass

    class AlreadyActiveStore(MetadataRefreshStore):
        @override
        async def begin_metadata_run(
            self,
            principal_id: str,
            drive_id: str,
            selection: MetadataSelection,
            operation_id: UUID | None = None,
        ) -> MetadataIngestRun:
            _ = (principal_id, drive_id, selection, operation_id)
            raise SpecializedRunAlreadyActive("metadata run is already active")

    monkeypatch.setattr(
        metadata_route,
        "config",
        SimpleNamespace(stream_progress_interval_seconds=1),
    )

    async def collect():
        return [
            event
            async for event in ingest_group_metadata_endpoint(
                "group",
                MetadataIngestRequestBody(),
                "user",
                OneGroupSource(),
                cast(DocumentRefreshSource, cast(object, AsyncMock())),
                AlreadyActiveStore(),
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == ["started", "error"]
    operation_id = events[0].data["operation_id"]
    assert events[1].data == {
        "status": "error",
        "operation_id": operation_id,
        "error": {
            "code": "metadata.run_already_active",
            "http_status": 409,
            "detail": "A metadata run is already active",
        },
        "context": {
            "operation": "metadata.ingest",
            "group_id": "group",
            "drive_id": "bad-drive",
            "operation_id": operation_id,
        },
        "progress": {"processed": 0},
    }


def test_metadata_endpoint_skips_inaccessible_drive_and_completes() -> None:
    class PartlyAccessibleSource:
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
            assert cursor is None
            assert root_item_id == "root"
            assert (extensions, include_paths, exclude_paths, exclude_extensions) == (
                [],
                [],
                [],
                [],
            )
            if drive_id == "bad-drive":
                raise MetadataSourceError(403, "Access denied")
            return DocumentMetadataScan(
                (
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
                ),
                (),
                "new-cursor",
                False,
            )

    async def collect():
        return [
            event
            async for event in ingest_group_metadata_endpoint(
                "group",
                MetadataIngestRequestBody(),
                "user",
                OneGroupSource(),
                PartlyAccessibleSource(),
                MetadataRefreshStore(),
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == [
        "started",
        "skipped",
        "progress",
        "complete",
    ]
    operation_id = events[0].data["operation_id"]
    assert events[1].data == {
        "status": "skipped",
        "operation_id": operation_id,
        "group_id": "group",
        "drive_id": "bad-drive",
        "processed": 0,
        "error_status": 403,
        "detail": "Drive is not accessible",
    }
    assert events[2].data == {
        "status": "progress",
        "operation_id": operation_id,
        "group_id": "group",
        "drive_id": "drive",
        "processed": 1,
        "total": 1,
        "available": 1,
        "item_id": "item",
        "name": "Report.pdf",
    }
    assert events[3].data == {
        "status": "complete",
        "operation_id": operation_id,
        "group_id": "group",
        "processed": 1,
        "skipped_drive_ids": ["bad-drive"],
    }


def test_metadata_endpoint_reports_explicitly_included_drive_denial() -> None:
    class DeniedSource:
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
            assert cursor is None
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
            async for event in ingest_group_metadata_endpoint(
                "group",
                MetadataIngestRequestBody(
                    include=[DriveMetadataSelectorBody(drive_id="bad-drive")]
                ),
                "user",
                OneGroupSource(),
                DeniedSource(),
                MetadataRefreshStore(),
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == ["started", "error"]
    assert events[1].data["context"]["drive_id"] == "bad-drive"
    assert events[1].data["error"] == {
        "code": "metadata.source_error",
        "http_status": 403,
        "detail": "Metadata source request failed",
    }


def test_metadata_endpoint_reports_explicit_drive_filtered_by_discovery() -> None:
    async def collect():
        return [
            event
            async for event in ingest_group_metadata_endpoint(
                "group",
                MetadataIngestRequestBody(
                    include=[DriveMetadataSelectorBody(drive_id="hidden-drive")]
                ),
                "user",
                OneGroupSource(),
                cast(DocumentRefreshSource, cast(object, AsyncMock())),
                cast(DocumentRefreshStore, cast(object, AsyncMock())),
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == ["started", "error"]
    assert events[1].data["context"]["drive_id"] == "hidden-drive"
    assert events[1].data["error"] == {
        "code": "metadata.source_error",
        "http_status": 403,
        "detail": "Metadata source request failed",
    }
    assert events[1].data["progress"]["processed"] == 0


def test_metadata_duplicate_selection_has_specific_public_error() -> None:
    async def collect():
        return [
            event
            async for event in ingest_group_metadata_endpoint(
                "group",
                MetadataIngestRequestBody(
                    include=[
                        DriveMetadataSelectorBody(drive_id="drive"),
                        DriveMetadataSelectorBody(drive_id="drive"),
                    ]
                ),
                "user",
                OneGroupSource(),
                cast(DocumentRefreshSource, cast(object, AsyncMock())),
                MetadataRefreshStore(),
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == ["started", "error"]
    assert events[1].data["error"] == {
        "code": "metadata.invalid_selection",
        "http_status": 422,
        "detail": "include and exclude must each have unique drive_id values",
    }
    assert events[1].data["progress"] == {"processed": 0}


def test_metadata_endpoint_reports_and_logs_unexpected_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BrokenGroupSource:
        async def stream_user_groups(self, user_id: str) -> AsyncIterator[UserGroup]:
            if user_id:
                raise RuntimeError("discovery failed")
            yield UserGroup("unreachable", None)

    async def collect():
        return [
            event
            async for event in ingest_group_metadata_endpoint(
                "group",
                MetadataIngestRequestBody(),
                "user",
                cast(GroupDriveSource, cast(object, BrokenGroupSource())),
                cast(DocumentRefreshSource, cast(object, AsyncMock())),
                cast(DocumentRefreshStore, cast(object, AsyncMock())),
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == ["started", "error"]
    operation_id = events[0].data["operation_id"]
    assert events[1].data == {
        "status": "error",
        "operation_id": operation_id,
        "error": {
            "code": "internal_error",
            "http_status": 500,
            "detail": "The operation failed",
        },
        "context": {
            "operation": "metadata.ingest",
            "group_id": "group",
            "drive_id": None,
            "operation_id": operation_id,
        },
        "progress": {"processed": 0},
    }
    assert "discovery failed" not in str(events[1].data)
    failure_records = [record for record in caplog.records if record.exc_info]
    assert len(failure_records) == 1
    assert failure_records[0].exc_info is not None
    assert isinstance(failure_records[0].exc_info[1], RuntimeError)
    assert sum(record.name == "api.errors" for record in caplog.records) == 1
    assert "discovery failed" in caplog.text


def test_metadata_endpoint_logs_missing_outcome(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def empty_ingest(*args: object) -> AsyncIterator[object]:
        if len(args) == 0:
            yield object()

    monkeypatch.setattr(metadata_route, "ingest_group_metadata", empty_ingest)

    async def collect():
        return [
            event
            async for event in ingest_group_metadata_endpoint(
                "group",
                MetadataIngestRequestBody(),
                "user",
                OneGroupSource(),
                cast(DocumentRefreshSource, cast(object, AsyncMock())),
                cast(DocumentRefreshStore, cast(object, AsyncMock())),
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == ["error"]
    assert events[0].data["error"] == {
        "code": "internal_error",
        "http_status": 500,
        "detail": "The operation failed",
    }
    failure_records = [record for record in caplog.records if record.exc_info]
    assert not failure_records
    assert sum(record.name == "api.errors" for record in caplog.records) == 1
    assert "ended without an outcome" in caplog.text


def test_metadata_endpoint_streams_progress_and_completion() -> None:
    class OneDocumentSource:
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
            assert cursor is None
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
            return DocumentMetadataScan(
                (
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
                ),
                (),
                "new-cursor",
                False,
            )

    saved: list[Document] = []

    class Store:
        async def begin_group_operation(self, principal_id: str, group_id: str) -> UUID:
            _ = (principal_id, group_id)
            return uuid4()

        async def begin_metadata_run(
            self,
            principal_id: str,
            drive_id: str,
            selection: MetadataSelection,
            operation_id: UUID | None = None,
        ) -> MetadataIngestRun:
            _ = (principal_id, drive_id, selection, operation_id)
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

    async def collect():
        return [
            event
            async for event in ingest_group_metadata_endpoint(
                "group",
                MetadataIngestRequestBody(
                    include=[
                        DriveMetadataSelectorBody(
                            drive_id="drive", paths=[], extensions=["pdf"]
                        )
                    ]
                ),
                "user",
                OneGroupSource(),
                OneDocumentSource(),
                Store(),
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == ["started", "progress", "complete"]
    operation_id = events[0].data["operation_id"]
    assert events[1].data["operation_id"] == operation_id
    assert events[1].data["item_id"] == "item"
    assert events[1].data["total"] == 1
    assert events[1].data["available"] == 1
    assert events[2].data["operation_id"] == operation_id
    assert [document.id.id for document in saved] == [("drive", "item")]


def test_metadata_endpoint_accepts_include_paths_without_extensions() -> None:
    class EmptySource:
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
            assert cursor is None
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
            return DocumentMetadataScan((), (), "new-cursor", True)

    request = MetadataIngestRequestBody(
        include=[DriveMetadataSelectorBody(drive_id="drive", paths=["Reports/2026"])],
    )

    async def collect():
        return [
            event
            async for event in ingest_group_metadata_endpoint(
                "group",
                request,
                "user",
                OneGroupSource(),
                EmptySource(),
                MetadataRefreshStore(),
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == ["started", "complete"]
    assert events[1].data["processed"] == 0


def test_metadata_endpoint_close_discards_staged_refresh() -> None:
    old_document = Document(
        _id=DocumentId("drive", "old"),
        file_metadata=FileMetadata(
            name="Old.pdf",
            mime_type="application/pdf",
            size_bytes=10,
            content_version=ContentVersion("v1"),
            source_url=None,
        ),
        document_metadata=DocumentMetadata(),
    )
    new_document = Document(
        _id=DocumentId("drive", "new"),
        file_metadata=FileMetadata(
            name="New.pdf",
            mime_type="application/pdf",
            size_bytes=10,
            content_version=ContentVersion("v2"),
            source_url=None,
        ),
        document_metadata=DocumentMetadata(),
    )

    class RefreshSource:
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
            _ = (
                drive_id,
                extensions,
                include_paths,
                exclude_paths,
                exclude_extensions,
                root_item_id,
            )
            assert cursor == "old-cursor"
            return DocumentMetadataScan((new_document,), (), "new-cursor", False)

    class RefreshStore:
        def __init__(self) -> None:
            self.staged: dict[str, Document] = {}
            self.published: dict[str, Document] = {"old": old_document}
            self.cursor: str | None = "old-cursor"
            self.failed: bool = False
            self.operation_id: UUID | None = None

        async def begin_group_operation(self, principal_id: str, group_id: str) -> UUID:
            _ = (principal_id, group_id)
            self.operation_id = uuid4()
            return self.operation_id

        async def begin_metadata_run(
            self,
            principal_id: str,
            drive_id: str,
            selection: MetadataSelection,
            operation_id: UUID | None = None,
        ) -> MetadataIngestRun:
            _ = (principal_id, drive_id, selection, operation_id)
            return MetadataIngestRun(uuid4(), self.cursor, True)

        async def stage_document(self, run_id: object, document: Document) -> None:
            _ = run_id
            self.staged[document.id.id[1]] = document

        async def stage_deleted_item(self, run_id: object, item_id: str) -> None:
            _ = (run_id, item_id)

        async def publish_metadata_run(
            self, run_id: object, delta_link: str, baseline: bool, reconcile: bool
        ) -> None:
            _ = (run_id, delta_link, baseline, reconcile)
            self.published.update(self.staged)
            self.cursor = delta_link

        async def publish_metadata_runs(
            self,
            publications: tuple[MetadataRunPublication, ...],
            operation_id: UUID | None = None,
            summary: dict[str, object] | None = None,
            domain_ids: dict[str, object] | None = None,
        ) -> None:
            assert operation_id == self.operation_id
            _ = (summary, domain_ids)
            self.published.update(self.staged)
            self.cursor = publications[-1].delta_link

        async def fail_group_operation(
            self,
            operation_id: UUID,
            failure_code: str,
            *,
            interrupted: bool = False,
            summary: dict[str, object] | None = None,
        ) -> None:
            assert operation_id == self.operation_id
            _ = (failure_code, interrupted, summary)

        async def discard_metadata_run(self, run_id: object) -> None:
            _ = run_id
            self.staged.clear()
            self.failed = True

    async def close_after_first_progress() -> None:
        store = RefreshStore()
        endpoint = ingest_group_metadata_endpoint(
            "group",
            MetadataIngestRequestBody(),
            "user",
            OneGroupSource(),
            cast(DocumentRefreshSource, cast(object, RefreshSource())),
            cast(DocumentRefreshStore, cast(object, store)),
        )

        started = await anext(endpoint)
        assert started.event == "started"
        event = await anext(endpoint)
        assert event.event == "progress"
        assert store.staged == {"new": new_document}
        await endpoint.aclose()

        assert store.failed
        assert store.staged == {}
        assert store.published == {"old": old_document}
        assert store.cursor == "old-cursor"

    asyncio.run(close_after_first_progress())
