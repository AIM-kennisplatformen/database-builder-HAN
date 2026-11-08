from collections.abc import AsyncIterator

from domain.exceptions.employee_sync_errors import (
    InvalidLimit,
    NoEmployeesFound,
    SourceUnavailable,
    StoreUnavailable,
)
from domain.models.employee import Employee, SyncEmployeesProgress, SyncEmployeesRequest
from domain.ports.persistence.employee_store import EmployeeStore
from domain.ports.sources.employee_source import EmployeeSource


async def sync_employees(
    req: SyncEmployeesRequest, source: EmployeeSource, store: EmployeeStore
) -> AsyncIterator[SyncEmployeesProgress]:
    if req.limit is not None and req.limit <= 0:
        raise InvalidLimit("Employee sync limit must be positive")

    employees: list[Employee] = []
    total = 0
    available = 0
    try:
        async for employee, total, available in source.stream_employees(req.limit):
            employees.append(employee)
    except Exception as exc:
        raise SourceUnavailable("Employee source is unavailable") from exc

    if not employees:
        raise NoEmployeesFound("Employee sync found no employees")

    try:
        for processed, employee in enumerate(employees, 1):
            await store.save_employee(employee)
            yield SyncEmployeesProgress(
                processed=processed, total=total, available=available
            )
    except Exception as exc:
        raise StoreUnavailable("Employee store is unavailable") from exc
