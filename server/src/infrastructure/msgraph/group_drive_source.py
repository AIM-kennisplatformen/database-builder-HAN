from collections.abc import AsyncIterator
from typing import cast, override
from urllib.parse import quote

from httpx import Response
from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_abstractions.native_response_handler import NativeResponseHandler
from kiota_http.middleware.options import ResponseHandlerOption
from msgraph.graph_service_client import GraphServiceClient
from pydantic import BaseModel, Field

from domain.ports.sources.group_drive_source import GroupDriveSource, UserGroup
from infrastructure.msgraph.base import raise_for_graph_error, validate_graph_url


class GraphResource(BaseModel):
    id: str = Field(min_length=1)
    odata_type: str | None = Field(default=None, alias="@odata.type")
    display_name: str | None = Field(default=None, alias="displayName")


class GraphResourcePage(BaseModel):
    value: list[GraphResource]
    next_link: str | None = Field(default=None, alias="@odata.nextLink")


def _parse_page(response: Response) -> GraphResourcePage:
    raise_for_graph_error(response, "Graph rejected the group drive request")
    return GraphResourcePage.model_validate_json(response.content)


class MsGraphGroupDriveSource(GroupDriveSource):
    def __init__(self, client: GraphServiceClient) -> None:
        self.client: GraphServiceClient = client

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
        builder = self.client.groups.by_group_id(group_id).drives
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
                return
            page = _parse_page(response)
            for drive in page.value:
                yield drive.id
            next_url = page.next_link
