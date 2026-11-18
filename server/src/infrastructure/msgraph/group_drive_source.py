from collections.abc import AsyncIterator
from typing import cast, override
from urllib.parse import quote

from httpx import Response
from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_abstractions.native_response_handler import NativeResponseHandler
from kiota_http.middleware.options import ResponseHandlerOption
from msgraph.graph_service_client import GraphServiceClient
from pydantic import BaseModel, Field

from domain.ports.documents.group_drive_source import (
    GroupDriveLocation,
    GroupDriveSource,
    UserGroup,
)
from infrastructure.msgraph.base import raise_for_graph_error, validate_graph_url


class GraphResource(BaseModel):
    id: str = Field(min_length=1)
    name: str | None = None
    odata_type: str | None = Field(default=None, alias="@odata.type")
    display_name: str | None = Field(default=None, alias="displayName")
    membership_type: str | None = Field(default=None, alias="membershipType")


class GraphResourcePage(BaseModel):
    value: list[GraphResource]
    next_link: str | None = Field(default=None, alias="@odata.nextLink")


class GraphChannelFolderParent(BaseModel):
    drive_id: str = Field(min_length=1, alias="driveId")


class GraphChannelFolder(BaseModel):
    id: str = Field(min_length=1)
    parent_reference: GraphChannelFolderParent = Field(alias="parentReference")


def _parse_page(response: Response) -> GraphResourcePage:
    raise_for_graph_error(response, "Graph rejected the group drive request")
    return GraphResourcePage.model_validate_json(response.content)


class MsGraphGroupDriveSource(GroupDriveSource):
    def __init__(self, client: GraphServiceClient) -> None:
        self.client: GraphServiceClient = client

    async def _can_scan_drive(self, drive_id: str) -> bool:
        probe_url = (
            f"https://graph.microsoft.com/v1.0/drives/{quote(drive_id, safe='')}"
            "/root/delta"
        )
        delta = (
            self.client.drives.by_drive_id(drive_id)
            .items.by_drive_item_id("root")
            .delta
        )
        response = cast(
            Response | None,
            await delta.with_url(probe_url).get(
                request_configuration=RequestConfiguration(
                    options=[ResponseHandlerOption(NativeResponseHandler())]
                )
            ),
        )
        if response is None:
            raise ValueError("Graph returned no drive access response")
        if response.status_code in {403, 404}:
            return False
        raise_for_graph_error(response, "Graph rejected the drive access request")
        return True

    async def _get_drive_name(self, drive_id: str) -> str | None:
        drive_url = (
            f"https://graph.microsoft.com/v1.0/drives/{quote(drive_id, safe='')}"
        )
        response = cast(
            Response | None,
            await self.client.drives.by_drive_id(drive_id)
            .with_url(drive_url)
            .get(
                request_configuration=RequestConfiguration(
                    options=[ResponseHandlerOption(NativeResponseHandler())]
                )
            ),
        )
        if response is None:
            raise ValueError("Graph returned no drive metadata response")
        if response.status_code in {403, 404}:
            return None
        raise_for_graph_error(response, "Graph rejected the drive metadata request")
        return GraphResource.model_validate_json(response.content).name

    @override
    async def stream_user_groups(self, user_id: str) -> AsyncIterator[UserGroup]:
        builder = self.client.users.by_user_id(user_id).member_of
        next_url: str | None = (
            f"https://graph.microsoft.com/v1.0/users/{quote(user_id, safe='')}/memberOf"
        )
        seen_urls: set[str] = set()
        while next_url is not None:
            validate_graph_url(next_url)
            if next_url in seen_urls:
                raise ValueError("Graph repeated an @odata.nextLink")
            seen_urls.add(next_url)
            response = cast(
                Response | None,
                await builder.with_url(next_url).get(
                    request_configuration=RequestConfiguration(
                        options=[ResponseHandlerOption(NativeResponseHandler())]
                    )
                ),
            )
            if response is None:
                raise ValueError("Graph returned no membership page")
            page = _parse_page(response)
            for member in page.value:
                if member.odata_type == "#microsoft.graph.group":
                    yield UserGroup(member.id, member.display_name)
            next_url = page.next_link

    @override
    async def stream_group_drive_ids(self, group_id: str) -> AsyncIterator[str]:
        async for location in self.stream_group_drive_locations(group_id):
            yield location.drive_id

    @override
    async def stream_group_drive_locations(
        self, group_id: str
    ) -> AsyncIterator[GroupDriveLocation]:
        builder = self.client.groups.by_group_id(group_id).drives
        seen_drive_ids: set[str] = set()
        next_url: str | None = (
            f"https://graph.microsoft.com/v1.0/groups/{quote(group_id, safe='')}/drives"
        )
        seen_urls: set[str] = set()
        while next_url is not None:
            validate_graph_url(next_url)
            if next_url in seen_urls:
                raise ValueError("Graph repeated an @odata.nextLink")
            seen_urls.add(next_url)
            response = cast(
                Response | None,
                await builder.with_url(next_url).get(
                    request_configuration=RequestConfiguration(
                        options=[ResponseHandlerOption(NativeResponseHandler())]
                    )
                ),
            )
            if response is None:
                raise ValueError("Graph returned no group drive page")
            if response.status_code == 404:
                break
            page = _parse_page(response)
            for drive in page.value:
                if drive.id not in seen_drive_ids and await self._can_scan_drive(
                    drive.id
                ):
                    seen_drive_ids.add(drive.id)
                    yield GroupDriveLocation(drive.id, name=drive.name)
            next_url = page.next_link

        channel_builder = self.client.teams.by_team_id(group_id).channels
        next_url = f"https://graph.microsoft.com/v1.0/teams/{quote(group_id, safe='')}/channels"
        seen_urls = set()
        seen_channel_ids: set[str] = set()
        while next_url is not None:
            validate_graph_url(next_url)
            if next_url in seen_urls:
                raise ValueError("Graph repeated an @odata.nextLink")
            seen_urls.add(next_url)
            response = cast(
                Response | None,
                await channel_builder.with_url(next_url).get(
                    request_configuration=RequestConfiguration(
                        options=[ResponseHandlerOption(NativeResponseHandler())]
                    )
                ),
            )
            if response is None:
                raise ValueError("Graph returned no channel page")
            if response.status_code == 404:
                return  # A group without a Team has no channels.
            page = _parse_page(response)
            for channel in page.value:
                if channel.id in seen_channel_ids:
                    continue
                seen_channel_ids.add(channel.id)
                if channel.membership_type == "standard":
                    continue
                folder_url = (
                    f"https://graph.microsoft.com/v1.0/teams/{quote(group_id, safe='')}"
                    f"/channels/{quote(channel.id, safe='')}/filesFolder"
                )
                folder_builder = channel_builder.by_channel_id(channel.id).files_folder
                folder_response = cast(
                    Response | None,
                    await folder_builder.with_url(folder_url).get(
                        request_configuration=RequestConfiguration(
                            options=[ResponseHandlerOption(NativeResponseHandler())]
                        )
                    ),
                )
                if folder_response is None:
                    raise ValueError("Graph returned no channel files folder")
                if folder_response.status_code in {403, 404}:
                    continue
                raise_for_graph_error(
                    folder_response, "Graph rejected the channel files folder request"
                )
                folder = GraphChannelFolder.model_validate_json(folder_response.content)
                drive_id = folder.parent_reference.drive_id
                if drive_id not in seen_drive_ids:
                    seen_drive_ids.add(drive_id)
                    yield GroupDriveLocation(
                        drive_id, folder.id, await self._get_drive_name(drive_id)
                    )
            next_url = page.next_link
