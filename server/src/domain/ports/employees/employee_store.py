from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from domain.models.employee import Employee


class WriteEmployeeOutcome(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class EmployeeSyncCounts:
    created: int
    updated: int
    unchanged: int


class EmployeeWriter(Protocol):
    async def begin_sync(self, principal_id: str) -> UUID: ...

    async def stage_employee(
        self, run_id: UUID, employee: Employee
    ) -> WriteEmployeeOutcome: ...

    async def publish_sync(self, run_id: UUID) -> EmployeeSyncCounts: ...

    async def discard_sync(self, run_id: UUID, *, interrupted: bool = False) -> None: ...
