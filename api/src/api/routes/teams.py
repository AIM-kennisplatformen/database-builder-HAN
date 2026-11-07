"""HTTP routes for a caller's Teams and Team group document libraries."""

import mimetypes
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from api.schemas.team import TeamFileResponse, TeamFilesQuery, TeamResponse, TeamsQuery
from dependencies import get_team_service
from services.team_service import TeamService, TeamServiceError, TeamServiceErrorCode

router = APIRouter(tags=["Teams"])


ERROR_RESPONSES: dict[TeamServiceErrorCode, tuple[int, str]] = {
    TeamServiceErrorCode.INVALID_IDENTIFIER: (400, "Invalid identifier"),
    TeamServiceErrorCode.INVALID_GRAPH_RESPONSE: (502, "Invalid Graph response"),
    TeamServiceErrorCode.GRAPH_NOT_FOUND: (404, "Graph resource not found"),
    TeamServiceErrorCode.GRAPH_THROTTLED: (429, "Graph request throttled"),
    TeamServiceErrorCode.GRAPH_UNAUTHORIZED: (
        401,
        "Invalid or expired Graph bearer token",
    ),
    TeamServiceErrorCode.GRAPH_FORBIDDEN: (
        403,
        "Graph access denied or required consent missing",
    ),
    TeamServiceErrorCode.GRAPH_FAILED: (502, "Graph request failed"),
    TeamServiceErrorCode.GRAPH_CONNECTION_FAILED: (502, "Graph connection failed"),
    TeamServiceErrorCode.NOT_TEAM_MEMBER: (403, "Caller does not belong to this Team"),
    TeamServiceErrorCode.DRIVE_OUTSIDE_TEAM: (
        403,
        "Drive does not belong to this Team",
    ),
    TeamServiceErrorCode.ITEM_OUTSIDE_DRIVE: (
        403,
        "Item does not belong to this Team drive",
    ),
    TeamServiceErrorCode.NOT_A_FILE: (400, "Selected item is not a file"),
}


def handle_team_service_error(_request: Request, exception: Exception) -> JSONResponse:
    if not isinstance(exception, TeamServiceError):
        raise exception
    status, detail = ERROR_RESPONSES[exception.code]
    headers = None
    if exception.code == TeamServiceErrorCode.GRAPH_THROTTLED:
        headers = {"Retry-After": exception.retry_after or "1"}
    elif exception.code == TeamServiceErrorCode.GRAPH_UNAUTHORIZED:
        headers = {"WWW-Authenticate": "Bearer"}
    return JSONResponse(status_code=status, content={"detail": detail}, headers=headers)


@router.api_route("/teams", methods=["QUERY"], response_model=list[TeamResponse])
async def list_joined_teams(
    body: TeamsQuery,
    service: Annotated[TeamService, Depends(get_team_service)],
) -> list[TeamResponse]:
    teams = await service.list_joined_teams(name=body.name)
    return [TeamResponse(id=team.id, display_name=team.display_name) for team in teams]


@router.api_route(
    "/teams/{team_id}/files", methods=["QUERY"], response_model=list[TeamFileResponse]
)
async def list_team_files(
    team_id: str,
    body: TeamFilesQuery,
    service: Annotated[TeamService, Depends(get_team_service)],
) -> list[TeamFileResponse]:
    files = await service.list_team_files(
        team_id, body.limit, body.recursive, path=body.path, name=body.name
    )
    return [
        TeamFileResponse(
            library=file.library,
            drive_id=file.drive_id,
            item_id=file.item_id,
            name=file.name,
            path=file.path,
            type=file.type,
            size=file.size,
            web_url=file.web_url,
        )
        for file in files
    ]


@router.get("/teams/{team_id}/files/{drive_id}/{item_id}/download")
async def download_team_file(
    team_id: str,
    drive_id: str,
    item_id: str,
    service: Annotated[TeamService, Depends(get_team_service)],
) -> Response:
    file = await service.download_team_file(team_id, drive_id, item_id)
    name = file.name
    safe_name = (
        re.sub(
            r"[^A-Za-z0-9._-]", "_", name if isinstance(name, str) else "download"
        ).strip(".")
        or "download"
    )
    mime = file.mime_type
    content_type = (
        mime or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    )
    if "\r" in content_type or "\n" in content_type:
        content_type = "application/octet-stream"

    return Response(
        content=file.content,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )
