import asyncio
from collections.abc import AsyncIterator
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from msgraph.graph_service_client import GraphServiceClient

from api.routes.documents.router import router as documents_router
from dependencies import (
    get_current_graph_user_id,
    get_group_drive_source,
)
from domain.ports.sources.group_drive_source import GroupDriveSource, UserGroup
from domain.use_cases.group_drive_discovery import get_user_group_drives
from infrastructure.msgraph.group_drive_source import MsGraphGroupDriveSource


class FakeGroupDriveSource:
    def stream_user_groups(self, user_id: str) -> AsyncIterator[UserGroup]:
        async def groups() -> AsyncIterator[UserGroup]:
            assert user_id == "user"
            for group in (
                UserGroup("group-a", "Research"),
                UserGroup("group-a", "Research"),
                UserGroup("group-b", "Teaching"),
            ):
                yield group

        return groups()

    def stream_group_drive_ids(self, group_id: str) -> AsyncIterator[str]:
        async def drives() -> AsyncIterator[str]:
            for drive_id in {
                "group-a": ("drive-1", "drive-2"),
                "group-b": ("drive-2", "drive-3"),
            }[group_id]:
                yield drive_id

        return drives()


def test_use_case_deduplicates_groups_and_drives() -> None:
    groups = asyncio.run(get_user_group_drives("user", FakeGroupDriveSource()))

    assert [
        (group.group_id, group.group_name, group.drive_ids) for group in groups
    ] == [
        ("group-a", "Research", ("drive-1", "drive-2")),
        ("group-b", "Teaching", ("drive-2", "drive-3")),
    ]


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
                    "value": [{"id": "drive-1"}],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/drives-next",
                },
            ),
            Response(200, json={"value": [{"id": "drive-2"}]}),
        ]
    )
    client = MagicMock()
    client.users.by_user_id.return_value.member_of = memberships
    client.groups.by_group_id.return_value.drives = drives
    source = MsGraphGroupDriveSource(cast(GraphServiceClient, client))

    groups = asyncio.run(get_user_group_drives("user", source))

    assert len(groups) == 1
    assert groups[0].group_id == "group"
    assert groups[0].group_name == "Research"
    assert groups[0].drive_ids == ("drive-1", "drive-2")
    assert memberships.with_url.call_count == 2
    assert drives.with_url.call_count == 2


def test_current_user_id_comes_from_graph_me() -> None:
    client = MagicMock()
    client.me.get = AsyncMock(return_value=MagicMock(id="user"))

    user_id = asyncio.run(get_current_graph_user_id(cast(GraphServiceClient, client)))

    assert user_id == "user"
    client.me.get.assert_awaited_once()


def test_group_drive_route_returns_drive_ids() -> None:
    app = FastAPI()
    app.include_router(documents_router)

    async def current_user_id() -> str:
        return "user"

    async def group_drive_source() -> GroupDriveSource:
        return FakeGroupDriveSource()

    app.dependency_overrides[get_current_graph_user_id] = current_user_id
    app.dependency_overrides[get_group_drive_source] = group_drive_source

    async def request() -> Response:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.get("/api/v1/documents/drives")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert response.json() == {
        "groups": [
            {
                "id": "group-a",
                "name": "Research",
                "drive_ids": ["drive-1", "drive-2"],
            },
            {
                "id": "group-b",
                "name": "Teaching",
                "drive_ids": ["drive-2", "drive-3"],
            },
        ],
    }


def test_group_without_drive_is_skipped() -> None:
    drives = MagicMock()
    drives.with_url.return_value.get = AsyncMock(
        return_value=Response(
            404, json={"error": {"code": "itemNotFound", "message": "No drive"}}
        )
    )
    client = MagicMock()
    client.groups.by_group_id.return_value.drives = drives
    source = MsGraphGroupDriveSource(cast(GraphServiceClient, client))

    async def collect() -> list[str]:
        return [drive_id async for drive_id in source.stream_group_drive_ids("group")]

    assert asyncio.run(collect()) == []
