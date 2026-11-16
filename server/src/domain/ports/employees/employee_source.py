from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from domain.models.employee import Employee


@dataclass(frozen=True)
class EmployeeStreamItem:
    employee: Employee
    total: int
    available: int


class EmployeeSource(Protocol):
    def stream_employees(
        self, limit: int | None
    ) -> AsyncIterator[EmployeeStreamItem]: ...
