"""Microsoft Graph SDK access to Teams and their document libraries."""

import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast
from urllib.parse import urlparse

import httpx
from kiota_abstractions.api_error import APIError
from msgraph.generated.models.directory_object import DirectoryObject
from msgraph.generated.models.drive import Drive
from msgraph.generated.models.drive_item import DriveItem
from msgraph.generated.models.group import Group
from msgraph.graph_service_client import GraphServiceClient

TEAM_ID = re.compile(r"^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
ITEM_ID = re.compile(r"^[A-Za-z0-9_!.-]+$")
GRAPH = "https://graph.microsoft.com/v1.0"


@dataclass(frozen=True)
class TeamSummary:
    id: str
    display_name: str


@dataclass(frozen=True)
class TeamFile:
    library: str
    drive_id: str
    item_id: str
    name: str
    path: str
    type: str
    size: int | None
    web_url: str | None


@dataclass(frozen=True)
class TeamFileDownload:
    name: str | None
    mime_type: str | None
    content: bytes


class TeamServiceErrorCode(StrEnum):
    INVALID_IDENTIFIER = "invalid_identifier"
    INVALID_GRAPH_RESPONSE = "invalid_graph_response"
    GRAPH_NOT_FOUND = "graph_not_found"
    GRAPH_THROTTLED = "graph_throttled"
    GRAPH_UNAUTHORIZED = "graph_unauthorized"
    GRAPH_FORBIDDEN = "graph_forbidden"
    GRAPH_FAILED = "graph_failed"
    GRAPH_CONNECTION_FAILED = "graph_connection_failed"
    NOT_TEAM_MEMBER = "not_team_member"
    DRIVE_OUTSIDE_TEAM = "drive_outside_team"
    ITEM_OUTSIDE_DRIVE = "item_outside_drive"
    NOT_A_FILE = "not_a_file"


class TeamServiceError(Exception):
    def __init__(
        self, code: TeamServiceErrorCode, *, retry_after: str | None = None
    ) -> None:
        super().__init__(code.value)
        self.code: TeamServiceErrorCode = code
        self.retry_after: str | None = retry_after


class GraphPage[Item](Protocol):
    value: list[Item] | None
    odata_next_link: str | None


def _validate_identifier(value: str, *, team: bool = False) -> str:
    if (
        not value
        or value in {".", ".."}
        or not (TEAM_ID if team else ITEM_ID).fullmatch(value)
    ):
        raise TeamServiceError(TeamServiceErrorCode.INVALID_IDENTIFIER)
    return value


def _validate_next_link(url: str) -> str:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "graph.microsoft.com"
        or not parsed.path.startswith("/v1.0/")
        or parsed.fragment
    ):
        raise TeamServiceError(TeamServiceErrorCode.INVALID_GRAPH_RESPONSE)
    return url


def _graph_error(error: APIError) -> TeamServiceError:
    status = error.response_status_code
    if status == 404:
        return TeamServiceError(TeamServiceErrorCode.GRAPH_NOT_FOUND)
    if status == 429:
        headers = error.response_headers or {}
        return TeamServiceError(
            TeamServiceErrorCode.GRAPH_THROTTLED,
            retry_after=headers.get("Retry-After", "1"),
        )
    if status == 401:
        return TeamServiceError(TeamServiceErrorCode.GRAPH_UNAUTHORIZED)
    if status == 403:
        return TeamServiceError(TeamServiceErrorCode.GRAPH_FORBIDDEN)
    return TeamServiceError(TeamServiceErrorCode.GRAPH_FAILED)


async def _fetch[Item](fetch: Callable[[], Awaitable[Item | None]]) -> Item:
    try:
        result = await fetch()
    except APIError as error:
        raise _graph_error(error) from error
    except httpx.HTTPError as error:
        raise TeamServiceError(TeamServiceErrorCode.GRAPH_CONNECTION_FAILED) from error
    if result is None:
        raise TeamServiceError(TeamServiceErrorCode.INVALID_GRAPH_RESPONSE)
    return result


async def _iter_collection[Item](
    first_page: Callable[[], Awaitable[GraphPage[Item] | None]],
    next_page: Callable[[str], Awaitable[GraphPage[Item] | None]],
) -> AsyncIterator[Item]:
    page = await _fetch(first_page)
    seen: set[str] = set()
    while True:
        if page.value is None:
            raise TeamServiceError(TeamServiceErrorCode.INVALID_GRAPH_RESPONSE)
        for item in page.value:
            yield item
        url = page.odata_next_link
        if not url:
            return
        _ = _validate_next_link(url)
        if url in seen:
            raise TeamServiceError(TeamServiceErrorCode.INVALID_GRAPH_RESPONSE)
        seen.add(url)
        page = await _fetch(lambda url=url: next_page(url))


class TeamService:
    def __init__(self, client: GraphServiceClient) -> None:
        self.client: GraphServiceClient = client

    async def _member_groups(self) -> AsyncIterator[DirectoryObject]:
        # The generated /me builder currently emits a placeholder user ID.
        members = self.client.me.member_of.with_url(f"{GRAPH}/me/memberOf")
        async for member in _iter_collection(
            members.get, lambda url: members.with_url(url).get()
        ):
            if isinstance(member, Group):
                yield member

    async def list_joined_teams(self, name: str | None = None) -> list[TeamSummary]:
        result: list[TeamSummary] = []
        name_filter = name.casefold() if name is not None else None
        async for member in self._member_groups():
            if not member.id:
                raise TeamServiceError(TeamServiceErrorCode.INVALID_GRAPH_RESPONSE)
            group_id = _validate_identifier(member.id, team=True)
            group = await _fetch(
                lambda group_id=group_id: self.client.groups.by_group_id(group_id).get()
            )
            if "Team" not in (group.resource_provisioning_options or []):
                continue
            if not group.display_name:
                raise TeamServiceError(TeamServiceErrorCode.INVALID_GRAPH_RESPONSE)
            if name_filter is None or name_filter in group.display_name.casefold():
                result.append(
                    TeamSummary(id=member.id, display_name=group.display_name)
                )
        return result

    async def verify_team_membership(self, team_id: str) -> None:
        _ = _validate_identifier(team_id, team=True)
        async for member in self._member_groups():
            if member.id and member.id.lower() == team_id.lower():
                group = await _fetch(
                    lambda: self.client.groups.by_group_id(team_id).get()
                )
                if "Team" in (group.resource_provisioning_options or []):
                    return
                break
        raise TeamServiceError(TeamServiceErrorCode.NOT_TEAM_MEMBER)

    async def iter_team_drives(self, team_id: str) -> AsyncIterator[Drive]:
        drives = self.client.groups.by_group_id(
            _validate_identifier(team_id, team=True)
        ).drives
        async for drive in _iter_collection(
            drives.get, lambda url: drives.with_url(url).get()
        ):
            if not drive.id or not drive.name:
                raise TeamServiceError(TeamServiceErrorCode.INVALID_GRAPH_RESPONSE)
            yield drive

    async def _iter_folder_items(
        self, drive_id: str, folder_id: str
    ) -> AsyncIterator[DriveItem]:
        children = (
            self.client.drives.by_drive_id(_validate_identifier(drive_id))
            .items.by_drive_item_id(folder_id)
            .children
        )
        async for item in _iter_collection(
            children.get, lambda url: children.with_url(url).get()
        ):
            yield item

    async def list_team_files(
        self,
        team_id: str,
        limit: int,
        recursive: bool,
        *,
        path: str | None = None,
        name: str | None = None,
    ) -> list[TeamFile]:
        await self.verify_team_membership(team_id)
        path_filter = path.casefold() if path is not None else None
        name_filter = name.casefold() if name is not None else None
        result: list[TeamFile] = []
        async for drive in self.iter_team_drives(team_id):
            drive_id = cast(str, drive.id)
            folders = [("root", "")]
            while folders:
                folder_id, prefix = folders.pop()
                async for item in self._iter_folder_items(drive_id, folder_id):
                    if not item.id or not item.name or "/" in item.name:
                        raise TeamServiceError(
                            TeamServiceErrorCode.INVALID_GRAPH_RESPONSE
                        )
                    if item.remote_item is not None:
                        continue
                    kind = (
                        "folder"
                        if item.folder is not None
                        else "file"
                        if item.file is not None
                        else "item"
                    )
                    item_path = prefix + item.name
                    matches_path = (
                        path_filter is None
                        or item_path.casefold().startswith(path_filter)
                    )
                    matches_name = name_filter is None or (
                        kind == "file" and name_filter in item.name.casefold()
                    )
                    if matches_path and matches_name:
                        result.append(
                            TeamFile(
                                library=cast(str, drive.name),
                                drive_id=drive_id,
                                item_id=item.id,
                                name=item.name,
                                path=item_path,
                                type=kind,
                                size=item.size,
                                web_url=item.web_url,
                            )
                        )
                        if len(result) >= limit:
                            return result
                    if recursive and kind == "folder":
                        folders.append((item.id, item_path + "/"))
        return result

    async def _get_team_file(
        self, team_id: str, drive_id: str, item_id: str
    ) -> DriveItem:
        await self.verify_team_membership(team_id)
        _ = _validate_identifier(drive_id)
        _ = _validate_identifier(item_id)
        async for drive in self.iter_team_drives(team_id):
            if drive.id == drive_id:
                break
        else:
            raise TeamServiceError(TeamServiceErrorCode.DRIVE_OUTSIDE_TEAM)
        item = await _fetch(
            lambda: (
                self.client.drives.by_drive_id(drive_id)
                .items.by_drive_item_id(item_id)
                .get()
            )
        )
        if (
            item.id != item_id
            or not item.parent_reference
            or item.parent_reference.drive_id != drive_id
            or item.remote_item is not None
        ):
            raise TeamServiceError(TeamServiceErrorCode.ITEM_OUTSIDE_DRIVE)
        if item.file is None:
            raise TeamServiceError(TeamServiceErrorCode.NOT_A_FILE)
        return item

    async def download_team_file(
        self, team_id: str, drive_id: str, item_id: str
    ) -> TeamFileDownload:
        item = await self._get_team_file(team_id, drive_id, item_id)
        content = await self._download_file_content(drive_id, item_id)
        return TeamFileDownload(
            name=item.name,
            mime_type=item.file.mime_type if item.file else None,
            content=content,
        )

    async def _download_file_content(self, drive_id: str, item_id: str) -> bytes:
        content = (
            self.client.drives.by_drive_id(_validate_identifier(drive_id))
            .items.by_drive_item_id(_validate_identifier(item_id))
            .content
        )
        return await _fetch(content.get)
