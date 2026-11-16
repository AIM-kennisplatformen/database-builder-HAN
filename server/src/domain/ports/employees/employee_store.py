from enum import StrEnum
from typing import Protocol

from domain.models.employee import Employee


class WriteEmployeeOutcome(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


class EmployeeWriter(Protocol):
    async def write_employee(self, employee: Employee) -> WriteEmployeeOutcome: ...
