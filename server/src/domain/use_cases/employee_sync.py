from collections.abc import AsyncIterator

from domain.exceptions.employee_sync_errors import (
    InvalidLimit,
    NoEmployeesFound,
    SourceUnavailable,
    StoreUnavailable,
)
from domain.models.employee import Employee, SyncEmployeesProgress, SyncEmployeesRequest
from domain.ports.persistence.employee_store import EmployeeStore, SaveEmployeeOutcome
from domain.ports.sources.employee_source import EmployeeSource


async def _stream_employee_source(
    source: EmployeeSource, limit: int | None
) -> AsyncIterator[tuple[Employee, int, int]]:
    try:
        async for employee, total, available in source.stream_employees(limit):
            yield employee, total, available
    except Exception as exc:
        raise SourceUnavailable("Employee source is unavailable") from exc


async def _save_employee(
    store: EmployeeStore, employee: Employee
) -> SaveEmployeeOutcome:
    try:
        return await store.save_employee(employee)
    except Exception as exc:
        raise StoreUnavailable("Employee store is unavailable") from exc


async def sync_employees(
    req: SyncEmployeesRequest, source: EmployeeSource, store: EmployeeStore
) -> AsyncIterator[SyncEmployeesProgress]:
    if req.limit is not None and req.limit <= 0:
        raise InvalidLimit("Employee sync limit must be positive")

    created = 0
    updated = 0
    unchanged = 0

    async for employee, total, available in _stream_employee_source(source, req.limit):
        match await _save_employee(store, employee):
            case SaveEmployeeOutcome.CREATED:
                created += 1
            case SaveEmployeeOutcome.UPDATED:
                updated += 1
            case SaveEmployeeOutcome.UNCHANGED:
                unchanged += 1
        yield SyncEmployeesProgress(
            total=total,
            available=available,
            created=created,
            updated=updated,
            unchanged=unchanged,
        )

    if created + updated + unchanged == 0:
        raise NoEmployeesFound("Employee sync found no employees")
