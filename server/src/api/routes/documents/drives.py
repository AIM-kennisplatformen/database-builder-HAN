from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from starlette.responses import JSONResponse

from api.errors import PublicError, log_api_failure
from api.routes.documents.error_mapping import MetadataErrorMapper
from dependencies import get_current_graph_user_id, get_group_drive_source
from domain.ports.documents.group_drive_source import GroupDriveSource
from domain.use_cases.group_drive_discovery import list_user_group_drives

router = APIRouter()


class DriveResponse(BaseModel):
    drive_id: str
    name: str | None


class GroupDrivesResponse(BaseModel):
    group_id: str
    group_name: str | None
    drives: list[DriveResponse]


def _discovery_error_response(public_error: PublicError) -> JSONResponse:
    return JSONResponse(
        status_code=public_error.http_status,
        content={"error": public_error.as_dict()},
    )


@router.get(
    "/groups/drives",
    response_model=list[GroupDrivesResponse],
    summary="List the current user's group drives",
)
async def get_group_drives_endpoint(
    user_id: Annotated[str, Depends(get_current_graph_user_id)],
    source: Annotated[GroupDriveSource, Depends(get_group_drive_source)],
) -> list[GroupDrivesResponse] | JSONResponse:
    try:
        groups = await list_user_group_drives(user_id, source)
    except Exception as exc:  # noqa: BLE001 - map all discovery failures to safe HTTP errors
        public_error = MetadataErrorMapper.describe(exc)
        log_api_failure(
            "metadata",
            exc,
            public_error,
            identifiers={"operation": "metadata.drive_discovery"},
        )
        return _discovery_error_response(public_error)
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
