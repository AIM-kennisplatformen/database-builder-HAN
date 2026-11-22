from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from domain.models.employee import Employee


@dataclass(frozen=True)
class EmployeeStreamItem:
    """Pair an employee record with the source's total and available counts."""

    employee: Employee
    total: int
    available: int


class EmployeeSource(Protocol):
    """Stream employee records and source counts for a synchronization."""

    def stream_employees(
        self, limit: int | None
    ) -> AsyncIterator[EmployeeStreamItem]:
        """Stream employee records with source counts.

        Args:
            limit: Maximum number of employees to yield, or ``None`` for no limit.

        Returns:
            An async iterator that yields employees and the source counts.
        """
        ...
