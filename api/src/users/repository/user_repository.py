"""Storage operations used by the user routes and Graph service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Protocol

from fastapi import Request

from users.user import User

if TYPE_CHECKING:
    from users.service.ms_graph_api_user_service import GraphUserSnapshot


class UserRepository(Protocol):
    def users(self, limit: int | None = None) -> AsyncIterator[User]: ...

    async def save_graph_snapshot(self, snapshot: GraphUserSnapshot) -> None: ...


def get_user_repository(request: Request) -> UserRepository:
    return request.app.state.user_repository
