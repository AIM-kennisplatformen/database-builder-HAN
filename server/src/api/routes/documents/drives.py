from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from dependencies import get_current_graph_user_id, get_group_drive_source
from domain.exceptions.metadata_inventory_errors import MetadataSourceError
from domain.ports.documents.group_drive_source import GroupDriveSource
from domain.use_cases.group_drive_discovery import (
    GroupNotAccessible,
    get_group_drives,
    get_user_groups,
)

router = APIRouter()


class GroupResponse(BaseModel):
    id: str
    name: str | None


class GroupsResponse(BaseModel):
    groups: list[GroupResponse]


class GroupDrivesResponse(BaseModel):
    id: str
    name: str | None
    drive_ids: list[str]


def _discovery_error(exc: Exception) -> HTTPException:
    if isinstance(exc, GroupNotAccessible):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, MetadataSourceError):
        status_code = exc.status_code if 400 <= exc.status_code < 500 else 502
        return HTTPException(status_code=status_code, detail=str(exc))
    return HTTPException(status_code=502, detail="Group discovery failed")


@router.get("/groups", summary="List the current user's groups")
async def get_groups_endpoint(
    user_id: Annotated[str, Depends(get_current_graph_user_id)],
    source: Annotated[GroupDriveSource, Depends(get_group_drive_source)],
) -> GroupsResponse:
    try:
        groups = await get_user_groups(user_id, source)
    except Exception as exc:
        raise _discovery_error(exc) from exc
    return GroupsResponse(
        groups=[GroupResponse(id=group.id, name=group.name) for group in groups]
    )


@router.get("/groups/{group_id}/drives", summary="List a member group's drives")
async def get_group_drives_endpoint(
    group_id: str,
    user_id: Annotated[str, Depends(get_current_graph_user_id)],
    source: Annotated[GroupDriveSource, Depends(get_group_drive_source)],
) -> GroupDrivesResponse:
    try:
        group = await get_group_drives(user_id, group_id, source)
    except Exception as exc:
        raise _discovery_error(exc) from exc
    return GroupDrivesResponse(
        id=group.group_id, name=group.group_name, drive_ids=list(group.drive_ids)
    )
