import asyncio
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from httpx import Response
from msgraph.graph_service_client import GraphServiceClient

from domain.ports.documents.group_drive_source import GroupDriveLocation
from infrastructure.msgraph.group_drive_source import GroupDriveSourceMsGraph


def test_duplicate_channel_drives_skip_name_lookup_before_deduplication() -> None:
    group_drives = MagicMock()
    group_drives.with_url.return_value.get = AsyncMock(
        return_value=Response(
            200,
            json={"value": [{"id": "shared-drive", "name": "Team files"}]},
        )
    )

    channels = MagicMock()
    channels.with_url.return_value.get = AsyncMock(
        return_value=Response(
            200,
            json={
                "value": [
                    {"id": "duplicate-team", "membershipType": "private"},
                    {"id": "first-private", "membershipType": "private"},
                    {"id": "duplicate-private", "membershipType": "shared"},
                ]
            },
        )
    )
    channel_folders = MagicMock()
    channel_folders.with_url.return_value.get = AsyncMock(
        side_effect=[
            Response(
                200,
                json={
                    "id": "team-root",
                    "parentReference": {"driveId": "shared-drive"},
                },
            ),
            Response(
                200,
                json={
                    "id": "private-root",
                    "parentReference": {"driveId": "private-drive"},
                },
            ),
            Response(
                200,
                json={
                    "id": "duplicate-root",
                    "parentReference": {"driveId": "private-drive"},
                },
            ),
        ]
    )

    client = MagicMock()
    client.groups.by_group_id.return_value.drives = group_drives
    client.teams.by_team_id.return_value.channels = channels
    client.teams.by_team_id.return_value.channels.by_channel_id.return_value.files_folder = channel_folders
    client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value.delta.with_url.return_value.get = AsyncMock(
        return_value=Response(
            200,
            json={"value": [], "@odata.deltaLink": "https://graph.microsoft.com/delta"},
        )
    )
    drive_name_lookups = [
        MagicMock(
            get=AsyncMock(
                return_value=Response(
                    200, json={"id": "private-drive", "name": "Private files"}
                )
            )
        ),
        MagicMock(
            get=AsyncMock(
                return_value=Response(
                    500, json={"error": {"message": "duplicate lookup must be skipped"}}
                )
            )
        ),
    ]
    client.drives.by_drive_id.return_value.with_url.side_effect = drive_name_lookups
    source = GroupDriveSourceMsGraph(cast(GraphServiceClient, client))

    async def collect() -> list[GroupDriveLocation]:
        return [
            location async for location in source.stream_group_drive_locations("team")
        ]

    locations = asyncio.run(collect())

    assert locations == [
        GroupDriveLocation("shared-drive", name="Team files"),
        GroupDriveLocation("private-drive", "private-root", "Private files"),
    ]
    client.drives.by_drive_id.return_value.with_url.assert_called_once_with(
        "https://graph.microsoft.com/v1.0/drives/private-drive"
    )
    drive_name_lookups[0].get.assert_awaited_once()
