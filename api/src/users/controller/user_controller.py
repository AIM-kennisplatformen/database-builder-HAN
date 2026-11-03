"""Source-independent user routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from users.repository.user_repository import UserRepository, get_user_repository
from users.service.user_service import UserService, get_user_service
from users.user import User

router = APIRouter(tags=["Users"])


@router.get("/users")
async def get_users(
    repository: Annotated[UserRepository, Depends(get_user_repository)],
    limit: Annotated[int, Query(ge=1)] = 10,
) -> list[User]:
    return [user async for user in repository.users(limit)]


@router.post("/users/refresh")
async def refresh_users(
    service: Annotated[UserService, Depends(get_user_service)],
    limit: Annotated[int | None, Query(ge=1)] = None,
) -> dict[str, int]:
    return {"saved": await service.retrieve_users_from_external_source(limit)}
