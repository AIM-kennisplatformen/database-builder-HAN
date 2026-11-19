import asyncio
from collections.abc import AsyncIterator
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from msgraph.graph_service_client import GraphServiceClient

from api.routes.documents.router import router as documents_router
from dependencies import (
    get_current_graph_user_id,
    get_group_drive_source,
)
from domain.exceptions.metadata_ingest_errors import MetadataSourceError
from domain.ports.documents.group_drive_source import (
    GroupDriveLocation,
    GroupDriveSource,
    UserGroup,
)
from domain.use_cases.group_drive_discovery import (
    GroupNotAccessible,
    get_group_drives,
    get_user_groups,
)
from infrastructure.msgraph.group_drive_source import MsGraphGroupDriveSource


class FakeGroupDriveSource:
    def stream_user_groups(self, user_id: str) -> AsyncIterator[UserGroup]:
        async def groups() -> AsyncIterator[UserGroup]:
            assert user_id == "user"
            for group in (
                UserGroup("group-a", "Research"),
                UserGroup("group-a", "Research"),
                UserGroup("group-b", "Teaching"),
                UserGroup("group-c", "No drive"),
            ):
                yield group

        return groups()

    def stream_group_drive_ids(self, group_id: str) -> AsyncIterator[str]:
        async def drives() -> AsyncIterator[str]:
            for drive_id in {
                "group-a": ("drive-1", "drive-2", "private-drive"),
                "group-b": ("drive-2", "drive-3"),
                "group-c": (),
            }[group_id]:
                yield drive_id

        return drives()

    async def stream_group_drive_locations(
        self, group_id: str
    ) -> AsyncIterator[GroupDriveLocation]:
        async for drive_id in self.stream_group_drive_ids(group_id):
            yield GroupDriveLocation(drive_id, name=f"Name of {drive_id}")


def test_use_cases_deduplicate_groups_and_list_only_requested_group_drives() -> None:
    source = FakeGroupDriveSource()
    groups = asyncio.run(get_user_groups("user", source))
    assert [(group.id, group.name) for group in groups] == [
        ("group-a", "Research"),
        ("group-b", "Teaching"),
        ("group-c", "No drive"),
    ]
    group = asyncio.run(get_group_drives("user", "group-a", source))
    assert group.drive_ids == ("drive-1", "drive-2", "private-drive")
    with pytest.raises(GroupNotAccessible):
        asyncio.run(get_group_drives("user", "unknown", source))


def test_graph_source_follows_membership_and_drive_pages() -> None:
    memberships = MagicMock()
    memberships.with_url.return_value.get = AsyncMock(
        side_effect=[
            Response(
                200,
                json={
                    "value": [
                        {"id": "role", "@odata.type": "#microsoft.graph.directoryRole"},
                        {
                            "id": "group",
                            "displayName": "Research",
                            "@odata.type": "#microsoft.graph.group",
                        },
                    ],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/members-next",
                },
            ),
            Response(200, json={"value": []}),
        ]
    )
    drives = MagicMock()
    drives.with_url.return_value.get = AsyncMock(
        side_effect=[
            Response(
                200,
                json={
                    "value": [{"id": "drive-1", "name": "Research documents"}],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/drives-next",
                },
            ),
            Response(200, json={"value": [{"id": "drive-2", "name": "Archive"}]}),
        ]
    )
    client = MagicMock()
    client.users.by_user_id.return_value.member_of = memberships
    client.groups.by_group_id.return_value.drives = drives
    client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value.delta.with_url.return_value.get = AsyncMock(
        return_value=Response(200, json={"value": [], "@odata.deltaLink": "done"})
    )
    client.teams.by_team_id.return_value.channels.with_url.return_value.get = AsyncMock(
        return_value=Response(200, json={"value": []})
    )
    source = MsGraphGroupDriveSource(cast(GraphServiceClient, client))

    group = asyncio.run(get_group_drives("user", "group", source))

    assert group.group_id == "group"
    assert group.group_name == "Research"
    assert group.drive_ids == ("drive-1", "drive-2")
    assert group.locations == (
        GroupDriveLocation("drive-1", name="Research documents"),
        GroupDriveLocation("drive-2", name="Archive"),
    )
    assert memberships.with_url.call_count == 2
    assert drives.with_url.call_count == 2


def test_graph_source_omits_drives_that_reject_delta_access() -> None:
    drives = MagicMock()
    drives.with_url.return_value.get = AsyncMock(
        return_value=Response(
            200,
            json={
                "value": [
                    {"id": "readable"},
                    {"id": "blocked"},
                    {"id": "also-readable"},
                ]
            },
        )
    )
    probe = AsyncMock(
        side_effect=[
            Response(200, json={"value": [], "@odata.deltaLink": "done"}),
            Response(403, json={"error": {"code": "accessDenied"}}),
            Response(200, json={"value": [], "@odata.deltaLink": "done"}),
        ]
    )
    client = MagicMock()
    client.groups.by_group_id.return_value.drives = drives
    delta = (
        client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value.delta
    )
    delta.with_url.return_value.get = probe
    client.teams.by_team_id.return_value.channels.with_url.return_value.get = AsyncMock(
        return_value=Response(200, json={"value": []})
    )
    source = MsGraphGroupDriveSource(cast(GraphServiceClient, client))

    async def collect() -> list[str]:
        return [drive_id async for drive_id in source.stream_group_drive_ids("group")]

    assert asyncio.run(collect()) == ["readable", "also-readable"]
    assert [call.args[0] for call in delta.with_url.call_args_list] == [
        f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/delta"
        for drive_id in ("readable", "blocked", "also-readable")
    ]


def test_graph_source_reports_drive_probe_server_failure() -> None:
    client = MagicMock()
    client.groups.by_group_id.return_value.drives.with_url.return_value.get = AsyncMock(
        return_value=Response(200, json={"value": [{"id": "drive"}]})
    )
    client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value.delta.with_url.return_value.get = AsyncMock(
        return_value=Response(503, json={"error": {"message": "Graph unavailable"}})
    )
    source = MsGraphGroupDriveSource(cast(GraphServiceClient, client))

    async def collect() -> list[str]:
        return [drive_id async for drive_id in source.stream_group_drive_ids("group")]

    with pytest.raises(MetadataSourceError, match="Graph unavailable"):
        asyncio.run(collect())


def test_graph_source_discovers_private_channel_drive_across_pages() -> None:
    drives = MagicMock()
    drives.with_url.return_value.get = AsyncMock(
        return_value=Response(
            200, json={"value": [{"id": "team-drive", "name": "Team files"}]}
        )
    )
    channels = MagicMock()
    channels.with_url.return_value.get = AsyncMock(
        side_effect=[
            Response(
                200,
                json={
                    "value": [
                        {"id": "standard-channel", "membershipType": "standard"},
                        {"id": "19:private@thread.tacv2", "membershipType": "private"},
                    ],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/next-channels",
                },
            ),
            Response(
                200,
                json={"value": [{"id": "shared-channel", "membershipType": "shared"}]},
            ),
        ]
    )
    folder = MagicMock()
    folder.with_url.return_value.get = AsyncMock(
        side_effect=[
            Response(
                200,
                json={
                    "id": "private-folder",
                    "parentReference": {"driveId": "private-drive"},
                },
            ),
            Response(
                200,
                json={
                    "id": "shared-folder",
                    "parentReference": {"driveId": "shared-drive"},
                },
            ),
        ]
    )
    client = MagicMock()
    client.groups.by_group_id.return_value.drives = drives
    client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value.delta.with_url.return_value.get = AsyncMock(
        return_value=Response(200, json={"value": [], "@odata.deltaLink": "done"})
    )
    drive_metadata = client.drives.by_drive_id.return_value.with_url.return_value
    drive_metadata.get = AsyncMock(
        side_effect=[
            Response(200, json={"id": "private-drive", "name": "Private files"}),
            Response(200, json={"id": "shared-drive", "name": "Shared files"}),
        ]
    )
    client.teams.by_team_id.return_value.channels = channels
    client.teams.by_team_id.return_value.channels.by_channel_id.return_value.files_folder = folder
    source = MsGraphGroupDriveSource(cast(GraphServiceClient, client))

    async def collect() -> list[GroupDriveLocation]:
        return [
            location async for location in source.stream_group_drive_locations("team")
        ]

    assert asyncio.run(collect()) == [
        GroupDriveLocation("team-drive", name="Team files"),
        GroupDriveLocation("private-drive", "private-folder", "Private files"),
        GroupDriveLocation("shared-drive", "shared-folder", "Shared files"),
    ]
    assert [call.args[0] for call in client.drives.by_drive_id.call_args_list] == [
        "team-drive",
        "private-drive",
        "shared-drive",
    ]
    assert drive_metadata.get.await_count == 2
    assert channels.with_url.call_count == 2
    assert folder.with_url.call_count == 2
    assert folder.with_url.call_args_list[0].args[0] == (
        "https://graph.microsoft.com/v1.0/teams/team/channels/"
        "19%3Aprivate%40thread.tacv2/filesFolder"
    )


def test_private_channel_drive_is_kept_without_a_drive_root_probe() -> None:
    client = MagicMock()
    client.groups.by_group_id.return_value.drives.with_url.return_value.get = AsyncMock(
        return_value=Response(200, json={"value": []})
    )
    channels = client.teams.by_team_id.return_value.channels
    channels.with_url.return_value.get = AsyncMock(
        return_value=Response(
            200,
            json={"value": [{"id": "private-channel", "membershipType": "private"}]},
        )
    )
    channels.by_channel_id.return_value.files_folder.with_url.return_value.get = (
        AsyncMock(
            return_value=Response(
                200,
                json={
                    "id": "channel-root",
                    "parentReference": {"driveId": "private-drive"},
                },
            )
        )
    )
    delta = (
        client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value.delta
    )

    delta.with_url.return_value.get = AsyncMock(
        return_value=Response(403, json={"error": {"code": "accessDenied"}})
    )
    client.drives.by_drive_id.return_value.with_url.return_value.get = AsyncMock(
        return_value=Response(403, json={"error": {"code": "accessDenied"}})
    )
    source = MsGraphGroupDriveSource(cast(GraphServiceClient, client))

    async def collect() -> list[GroupDriveLocation]:
        return [
            location async for location in source.stream_group_drive_locations("team")
        ]

    assert asyncio.run(collect()) == [
        GroupDriveLocation("private-drive", "channel-root")
    ]
    delta.with_url.assert_not_called()


def test_graph_source_skips_inaccessible_channel_folder() -> None:
    drives = MagicMock()
    drives.with_url.return_value.get = AsyncMock(
        return_value=Response(200, json={"value": [{"id": "team-drive"}]})
    )
    channels = MagicMock()
    channels.with_url.return_value.get = AsyncMock(
        return_value=Response(200, json={"value": [{"id": "private-channel"}]})
    )
    folder = MagicMock()
    folder.with_url.return_value.get = AsyncMock(
        return_value=Response(
            403, json={"error": {"code": "accessDenied", "message": "Denied"}}
        )
    )
    client = MagicMock()
    client.groups.by_group_id.return_value.drives = drives
    client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value.delta.with_url.return_value.get = AsyncMock(
        return_value=Response(200, json={"value": [], "@odata.deltaLink": "done"})
    )
    client.teams.by_team_id.return_value.channels = channels
    client.teams.by_team_id.return_value.channels.by_channel_id.return_value.files_folder = folder
    source = MsGraphGroupDriveSource(cast(GraphServiceClient, client))

    async def collect() -> list[str]:
        return [drive_id async for drive_id in source.stream_group_drive_ids("team")]

    assert asyncio.run(collect()) == ["team-drive"]


def test_current_user_id_comes_from_graph_me() -> None:
    client = MagicMock()
    client.me.get = AsyncMock(return_value=MagicMock(id="user"))

    user_id = asyncio.run(get_current_graph_user_id(cast(GraphServiceClient, client)))

    assert user_id == "user"
    client.me.get.assert_awaited_once()


def test_group_drives_route_lists_only_memberships_with_named_drives() -> None:
    app = FastAPI()
    app.include_router(documents_router)

    async def current_user_id() -> str:
        return "user"

    async def group_drive_source() -> GroupDriveSource:
        return FakeGroupDriveSource()

    app.dependency_overrides[get_current_graph_user_id] = current_user_id
    app.dependency_overrides[get_group_drive_source] = group_drive_source

    async def request() -> tuple[Response, Response, Response]:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            return (
                await client.get("/api/v1/documents/groups/drives"),
                await client.get("/api/v1/documents/groups"),
                await client.get("/api/v1/documents/groups/group-a/drives"),
            )

    group_drives, old_groups, old_group_drives = asyncio.run(request())

    assert group_drives.status_code == 200
    assert group_drives.json() == [
        {
            "group_id": "group-a",
            "group_name": "Research",
            "drives": [
                {"drive_id": "drive-1", "name": "Name of drive-1"},
                {"drive_id": "drive-2", "name": "Name of drive-2"},
                {"drive_id": "private-drive", "name": "Name of private-drive"},
            ],
        },
        {
            "group_id": "group-b",
            "group_name": "Teaching",
            "drives": [
                {"drive_id": "drive-2", "name": "Name of drive-2"},
                {"drive_id": "drive-3", "name": "Name of drive-3"},
            ],
        },
    ]
    assert old_groups.status_code == 404
    assert old_group_drives.status_code == 404


def test_group_without_drive_is_skipped() -> None:
    drives = MagicMock()
    drives.with_url.return_value.get = AsyncMock(
        return_value=Response(
            404, json={"error": {"code": "itemNotFound", "message": "No drive"}}
        )
    )
    client = MagicMock()
    client.groups.by_group_id.return_value.drives = drives
    client.teams.by_team_id.return_value.channels.with_url.return_value.get = AsyncMock(
        return_value=Response(404, json={"error": {"code": "itemNotFound"}})
    )
    source = MsGraphGroupDriveSource(cast(GraphServiceClient, client))

    async def collect() -> list[str]:
        return [drive_id async for drive_id in source.stream_group_drive_ids("group")]

    assert asyncio.run(collect()) == []
