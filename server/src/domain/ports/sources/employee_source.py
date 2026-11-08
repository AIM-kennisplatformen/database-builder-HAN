from collections.abc import AsyncIterator
from typing import Protocol

from domain.models.employee import Employee


class EmployeeSource(Protocol):
    def stream_employees(
        self, limit: int | None
    ) -> AsyncIterator[tuple[Employee, int, int]]: ...
