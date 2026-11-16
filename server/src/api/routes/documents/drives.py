from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from dependencies import get_current_graph_user_id, get_group_drive_source
from domain.exceptions.metadata_inventory_errors import MetadataSourceError
from domain.ports.documents.group_drive_source import GroupDriveSource
from domain.use_cases.group_drive_discovery import get_user_group_drives

router = APIRouter()


class GroupDrivesResponse(BaseModel):
    id: str
    name: str | None
    drive_ids: list[str]


class GroupDriveIdsResponse(BaseModel):
    groups: list[GroupDrivesResponse]


@router.get("/drives", summary="List drives from the current user's groups")
async def get_group_drives_endpoint(
    user_id: Annotated[str, Depends(get_current_graph_user_id)],
    source: Annotated[GroupDriveSource, Depends(get_group_drive_source)],
) -> GroupDriveIdsResponse:
    try:
        groups = await get_user_group_drives(user_id, source)
    except MetadataSourceError as exc:
        status_code = exc.status_code if 400 <= exc.status_code < 500 else 502
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Drive discovery failed") from exc
    return GroupDriveIdsResponse(
        groups=[
            GroupDrivesResponse(
                id=group.group_id,
                name=group.group_name,
                drive_ids=list(group.drive_ids),
            )
            for group in groups
        ],
    )
