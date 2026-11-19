from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from dependencies import get_current_graph_user_id, get_group_drive_source
from domain.exceptions.metadata_ingest_errors import MetadataSourceError
from domain.ports.documents.group_drive_source import GroupDriveSource
from domain.use_cases.group_drive_discovery import (
    GroupNotAccessible,
    get_user_group_drives,
)

router = APIRouter()


class DriveResponse(BaseModel):
    drive_id: str
    name: str | None


class GroupDrivesResponse(BaseModel):
    group_id: str
    group_name: str | None
    drives: list[DriveResponse]


def _discovery_error(exc: Exception) -> HTTPException:
    if isinstance(exc, GroupNotAccessible):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, MetadataSourceError):
        status_code = exc.status_code if 400 <= exc.status_code < 500 else 502
        return HTTPException(status_code=status_code, detail=str(exc))
    return HTTPException(status_code=502, detail="Group discovery failed")


@router.get("/groups/drives", summary="List the current user's group drives")
async def get_group_drives_endpoint(
    user_id: Annotated[str, Depends(get_current_graph_user_id)],
    source: Annotated[GroupDriveSource, Depends(get_group_drive_source)],
) -> list[GroupDrivesResponse]:
    try:
        groups = await get_user_group_drives(user_id, source)
    except Exception as exc:
        raise _discovery_error(exc) from exc
    return [
        GroupDrivesResponse(
            group_id=group.group_id,
            group_name=group.group_name,
            drives=[
                DriveResponse(drive_id=location.drive_id, name=location.name)
                for location in group.locations
            ],
        )
        for group in groups
    ]
