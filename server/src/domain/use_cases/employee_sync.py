from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass

from domain.exceptions.employee_sync_errors import (
    InvalidLimit,
    NoEmployeesFound,
    SourceUnavailable,
    StoreUnavailable,
)
from domain.models.employee import Employee
from domain.ports.employees.employee_source import EmployeeSource, EmployeeStreamItem
from domain.ports.employees.employee_store import EmployeeWriter, WriteEmployeeOutcome


@dataclass(frozen=True)
class EmployeesSyncRequest:
    limit: int | None = None


@dataclass(frozen=True)
class EmployeesSyncProgress:
    total: int
    available: int
    created: int
    updated: int
    unchanged: int

    @property
    def processed(self) -> int:
        return self.created + self.updated + self.unchanged


@dataclass(frozen=True)
class EmployeesSyncResult:
    total: int | None
    available: int
    created: int
    updated: int
    unchanged: int

    @property
    def processed(self) -> int:
        return self.created + self.updated + self.unchanged


async def _stream_employee_source(
    source: EmployeeSource, limit: int | None
) -> AsyncIterator[EmployeeStreamItem]:
    try:
        async for item in source.stream_employees(limit):
            yield item
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
) -> AsyncGenerator[EmployeesSyncProgress | EmployeesSyncResult]:
    if req.limit is not None and req.limit <= 0:
        raise InvalidLimit("Employee sync limit must be positive")

    created = 0
    updated = 0
    unchanged = 0

    total: int | None = None
    available = 0
    async for item in _stream_employee_source(source, req.limit):
        total = item.total
        available = item.available
        match await _write_employee(store, item.employee):
            case WriteEmployeeOutcome.CREATED:
                created += 1
            case WriteEmployeeOutcome.UPDATED:
                updated += 1
            case WriteEmployeeOutcome.UNCHANGED:
                unchanged += 1
        yield EmployeesSyncProgress(
            total=item.total,
            available=item.available,
            created=created,
            updated=updated,
            unchanged=unchanged,
        )

    if created + updated + unchanged == 0:
        raise NoEmployeesFound("Employee sync found no employees")

    yield EmployeesSyncResult(total, available, created, updated, unchanged)
