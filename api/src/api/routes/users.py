"""Source-independent user routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from api.schemas.user import RefreshUsersResponse, UserResponse
from dependencies import get_user_repository, get_user_service
from domain.repositories.user_repository import UserRepository
from services.user_service import MsGraphApiUserService

router = APIRouter(tags=["Users"])


@router.get(
    "/users",
    response_model=list[UserResponse],
    summary="List saved users",
    description="Read effective user profiles from PostgreSQL, including manual overrides.",
    response_description="Saved user profiles.",
)
async def get_users(
    repository: Annotated[UserRepository, Depends(get_user_repository)],
    limit: Annotated[int, Query(ge=1, description="Maximum number of users.")] = 10,
) -> list[UserResponse]:
    return [UserResponse.model_validate(user) async for user in repository.users(limit)]


@router.post(
    "/users/refresh",
    response_model=RefreshUsersResponse,
    summary="Refresh users from Microsoft Graph",
    description="Retrieve employee profiles from the configured Graph group and save new source versions.",
    response_description="Number of profiles saved.",
)
async def refresh_users(
    service: Annotated[MsGraphApiUserService, Depends(get_user_service)],
    limit: Annotated[
        int | None,
        Query(ge=1, description="Maximum profiles to retrieve; omit for all."),
    ] = None,
) -> RefreshUsersResponse:
    return RefreshUsersResponse(
        saved=await service.retrieve_han_employees_from_ms_graph(limit)
    )
