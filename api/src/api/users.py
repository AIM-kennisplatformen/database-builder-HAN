"""Source-independent user routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from api.dependencies import get_user_repository, get_user_service
from domain.repositories.user_repository import UserRepository
from schemas.user import RefreshUsersResponse, UserResponse
from services.user_service import MsGraphApiUserService

router = APIRouter(tags=["Users"])


@router.get("/users")
async def get_users(
    repository: Annotated[UserRepository, Depends(get_user_repository)],
    limit: Annotated[int, Query(ge=1)] = 10,
) -> list[UserResponse]:
    return [UserResponse.model_validate(user) async for user in repository.users(limit)]


@router.post("/users/refresh")
async def refresh_users(
    service: Annotated[MsGraphApiUserService, Depends(get_user_service)],
    limit: Annotated[int | None, Query(ge=1)] = None,
) -> RefreshUsersResponse:
    return RefreshUsersResponse(
        saved=await service.retrieve_users_from_external_source(limit)
    )
