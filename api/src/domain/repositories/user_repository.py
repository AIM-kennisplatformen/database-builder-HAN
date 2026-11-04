"""Storage operations used by the user routes and Graph service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from domain.models.user import User


class UserRepository(Protocol):
    def users(self, limit: int | None = None) -> AsyncIterator[User]: ...

    async def save_ms_graph_user(self, user: User) -> None: ...
