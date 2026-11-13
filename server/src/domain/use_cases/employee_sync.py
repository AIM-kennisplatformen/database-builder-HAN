from collections.abc import AsyncIterator

from domain.exceptions.employee_sync_errors import (
    InvalidLimit,
    NoEmployeesFound,
    SourceUnavailable,
    StoreUnavailable,
)
from domain.models.employee import Employee, EmployeesSyncProgress, EmployeesSyncRequest
from domain.ports.persistence.employee_store import EmployeeWriter, WriteEmployeeOutcome
from domain.ports.sources.employee_source import EmployeeSource


async def _stream_employee_source(
    source: EmployeeSource, limit: int | None
) -> AsyncIterator[tuple[Employee, int, int]]:
    try:
        async for employee, total, available in source.stream_employees(limit):
            yield employee, total, available
    except Exception as exc:
        raise SourceUnavailable("Employee source is unavailable") from exc


async def _write_employee(
    store: EmployeeWriter, employee: Employee
) -> WriteEmployeeOutcome:
    try:
        return await store.write_employee(employee)
    except Exception as exc:
        raise StoreUnavailable("Employee store is unavailable") from exc


async def sync_employees(
    req: EmployeesSyncRequest, source: EmployeeSource, store: EmployeeWriter
) -> AsyncIterator[EmployeesSyncProgress]:
    if req.limit is not None and req.limit <= 0:
        raise InvalidLimit("Employee sync limit must be positive")

    created = 0
    updated = 0
    unchanged = 0

    async for employee, total, available in _stream_employee_source(source, req.limit):
        match await _write_employee(store, employee):
            case WriteEmployeeOutcome.CREATED:
                created += 1
            case WriteEmployeeOutcome.UPDATED:
                updated += 1
            case WriteEmployeeOutcome.UNCHANGED:
                unchanged += 1
        yield EmployeesSyncProgress(
            total=total,
            available=available,
            created=created,
            updated=updated,
            unchanged=unchanged,
        )

    if created + updated + unchanged == 0:
        raise NoEmployeesFound("Employee sync found no employees")
