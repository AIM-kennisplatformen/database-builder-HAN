from collections.abc import AsyncGenerator
from dataclasses import dataclass
from uuid import UUID

import anyio

from domain.exceptions.employee_sync_errors import (
    InvalidLimit,
    NoEmployeesFound,
    SourceUnavailable,
    StoreUnavailable,
)
from domain.models.employee import Employee
from domain.ports.employees.employee_source import EmployeeSource, EmployeeStreamItem
from domain.ports.employees.employee_store import (
    EmployeeSyncCounts,
    EmployeeWriter,
    WriteEmployeeOutcome,
)


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
) -> AsyncGenerator[EmployeeStreamItem]:
    updates = source.stream_employees(limit)
    try:
        async for item in updates:
            yield item
    except Exception as exc:
        raise SourceUnavailable("Employee source is unavailable") from exc
    finally:
        close = getattr(updates, "aclose", None)
        if close is not None:
            with anyio.CancelScope(shield=True):
                await close()


async def _write_employee(
    store: EmployeeWriter, run_id: UUID, employee: Employee
) -> WriteEmployeeOutcome:
    try:
        return await store.stage_employee(run_id, employee)
    except Exception as exc:
        raise StoreUnavailable("Employee store is unavailable") from exc


async def _begin_sync(store: EmployeeWriter) -> UUID:
    run_id: UUID | None = None
    with anyio.CancelScope(shield=True):
        try:
            run_id = await store.begin_sync()
        except Exception as exc:
            raise StoreUnavailable("Employee store is unavailable") from exc
    if run_id is None:
        raise RuntimeError("Employee sync run was not created")
    return run_id


async def _publish_sync(store: EmployeeWriter, run_id: UUID) -> EmployeeSyncCounts:
    try:
        return await store.publish_sync(run_id)
    except Exception as exc:
        raise StoreUnavailable("Employee store is unavailable") from exc


async def _discard_sync(store: EmployeeWriter, run_id: UUID) -> None:
    try:
        await store.discard_sync(run_id)
    except Exception as exc:
        raise StoreUnavailable("Employee store is unavailable") from exc


async def sync_employees(
    req: EmployeesSyncRequest, source: EmployeeSource, store: EmployeeWriter
) -> AsyncGenerator[EmployeesSyncProgress | EmployeesSyncResult]:
    if req.limit is not None and req.limit <= 0:
        raise InvalidLimit("Employee sync limit must be positive")

    run_id = await _begin_sync(store)
    created = 0
    updated = 0
    unchanged = 0

    total: int | None = None
    available = 0
    published = False
    updates = _stream_employee_source(source, req.limit)
    try:
        async for item in updates:
            total = item.total
            available = item.available
            match await _write_employee(store, run_id, item.employee):
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

        counts = await _publish_sync(store, run_id)
        published = True
        yield EmployeesSyncResult(
            total,
            available,
            counts.created,
            counts.updated,
            counts.unchanged,
        )
    finally:
        with anyio.CancelScope(shield=True):
            try:
                await updates.aclose()
            finally:
                if not published:
                    await _discard_sync(store, run_id)
