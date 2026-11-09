from enum import StrEnum
from typing import Protocol

from domain.models.employee import Employee


class SaveEmployeeOutcome(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


class EmployeeStore(Protocol):
    async def save_employee(self, employee: Employee) -> SaveEmployeeOutcome: ...
