from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from domain.models.employee import Employee


class EmployeeWriteOutcome(StrEnum):
    """Classify an employee staged into a synchronization run."""

    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class EmployeeSyncCounts:
    """Count employee records by outcome after a sync run is published."""

    created: int
    updated: int
    unchanged: int


class EmployeeWriter(Protocol):
    """Stage employee data and publish or discard a synchronization run."""

    async def begin_sync(self, principal_id: str) -> UUID:
        """Create a staging run owned by the requesting principal.

        Args:
            principal_id: Identifier of the principal that initiated the sync.

        Returns:
            The identifier used by subsequent staging and publication calls.
        """
        ...

    async def stage_employee(
        self, run_id: UUID, employee: Employee
    ) -> EmployeeWriteOutcome:
        """Write an employee to staging without making it visible to readers.

        Args:
            run_id: Identifier of the sync run that owns the staged record.
            employee: Employee profile to stage.

        Returns:
            An outcome indicating whether the employee is new, changed, or unchanged.
        """
        ...

    async def publish_sync(self, run_id: UUID) -> EmployeeSyncCounts:
        """Atomically publish staged data and return its outcome counts.

        Args:
            run_id: Identifier of the sync run to publish.

        Returns:
            Counts of created, updated, and unchanged employee records.
        """
        ...

    async def discard_sync(
        self,
        run_id: UUID,
        *,
        interrupted: bool = False,
        failure_code: str = "employee.sync_failed",
        summary: dict[str, object] | None = None,
    ) -> None:
        """Discard staged data and record why the sync did not publish.

        Args:
            run_id: Identifier of the sync run to discard.
            interrupted: Whether the run ended because the request was interrupted.
            failure_code: Stable code describing the reason for failure.
            summary: Optional details to retain with the failed run.
        """
        ...


class EmployeeDataDeleter(Protocol):
    """Delete employee data when no active sync or user overrides block reset."""

    async def delete_all_employee_data(self) -> bool:
        """Delete employee data unless an active sync or user overrides block reset.

        Returns:
            ``True`` when reset succeeds, including when the data was already
            empty; ``False`` when an employee sync is running or user overrides
            prevent deletion.
        """
        ...
