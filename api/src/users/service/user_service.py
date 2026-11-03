"""Operations available through the user service."""

from typing import Protocol

from fastapi import Request


class UserService(Protocol):
    async def retrieve_users_from_external_source(
        self, limit: int | None = None
    ) -> int: ...


def get_user_service(request: Request) -> UserService:
    return request.app.state.user_service
