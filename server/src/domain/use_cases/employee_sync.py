from collections.abc import AsyncGenerator
from dataclasses import dataclass
from uuid import UUID

import anyio

from domain.exceptions.employee_sync_errors import (
    EmployeeSourceUnavailable,
    EmployeeStoreUnavailable,
    InvalidLimit,
    NoEmployeesFound,
)
from domain.models.employee import Employee
from domain.ports.employees.employee_source import EmployeeSource, EmployeeStreamItem
from domain.ports.employees.employee_store import (
    EmployeeSyncCounts,
    EmployeeWriteOutcome,
    EmployeeWriter,
)


@dataclass(frozen=True)
class EmployeeSyncRequest:
    """Configure an optional positive maximum for an employee synchronization."""

    limit: int | None = None


@dataclass(frozen=True)
class EmployeeSyncProgress:
    """Report accumulated write counts after staging an employee."""

    total: int
    available: int
    created: int
    updated: int
    unchanged: int
    operation_id: UUID

    @property
    def processed(self) -> int:
        """Return the number of records staged so far."""
        return self.created + self.updated + self.unchanged


@dataclass(frozen=True)
class EmployeeSyncStarted:
    """Identify the employee sync run before record progress is emitted."""

    operation_id: UUID


@dataclass(frozen=True)
class EmployeeSyncResult:
    """Report published counts after an employee sync completes."""

    total: int | None
    available: int
    created: int
    updated: int
    unchanged: int
    operation_id: UUID

    @property
    def processed(self) -> int:
        """Return the total number of records in the final counts."""
        return self.created + self.updated + self.unchanged


async def _stream_employee_source(
    source: EmployeeSource, limit: int | None
) -> AsyncGenerator[EmployeeStreamItem]:
    updates = source.stream_employees(limit)
    try:
        async for item in updates:
            yield item
    except Exception as exc:
        raise EmployeeSourceUnavailable("Employee source is unavailable") from exc
    finally:
        close = getattr(updates, "aclose", None)
        if close is not None:
            with anyio.CancelScope(shield=True):
                await close()


async def _write_employee(
    store: EmployeeWriter, run_id: UUID, employee: Employee
) -> EmployeeWriteOutcome:
    try:
        return await store.stage_employee(run_id, employee)
    except Exception as exc:
        raise EmployeeStoreUnavailable("Employee store is unavailable") from exc


async def _begin_sync(store: EmployeeWriter, principal_id: str) -> UUID:
    run_id: UUID | None = None
    with anyio.CancelScope(shield=True):
        try:
            run_id = await store.begin_sync(principal_id)
        except Exception as exc:
            raise EmployeeStoreUnavailable("Employee store is unavailable") from exc
    if run_id is None:
        raise RuntimeError("Employee sync run was not created")
    return run_id


async def _publish_sync(store: EmployeeWriter, run_id: UUID) -> EmployeeSyncCounts:
    try:
        return await store.publish_sync(run_id)
    except Exception as exc:
        raise EmployeeStoreUnavailable("Employee store is unavailable") from exc


async def _discard_sync(
    store: EmployeeWriter,
    run_id: UUID,
    *,
    interrupted: bool = False,
    failure_code: str = "employee.sync_failed",
    summary: dict[str, object] | None = None,
) -> None:
    try:
        await store.discard_sync(
            run_id,
            interrupted=interrupted,
            failure_code=failure_code,
            summary=summary,
        )
    except Exception as exc:
        raise EmployeeStoreUnavailable("Employee store is unavailable") from exc


async def sync_employees(
    req: EmployeeSyncRequest,
    source: EmployeeSource,
    store: EmployeeWriter,
    principal_id: str,
) -> AsyncGenerator[EmployeeSyncStarted | EmployeeSyncProgress | EmployeeSyncResult]:
    """Stream a staged employee sync and publish it only after records are read.

    Args:
        req: Optional positive maximum number of employees to synchronize; ``None``
            means the source is not limited by this request.
        source: Source that streams employee profiles.
        store: Writer that stages, publishes, or discards the run.
        principal_id: Principal to associate with the run.

    Yields:
        A started event, progress after each employee, and the published result.

    Raises:
        InvalidLimit: If a supplied limit is not positive.
        NoEmployeesFound: If the source yields no employee records.
        EmployeeSourceUnavailable: If reading the source fails.
        EmployeeStoreUnavailable: If a store operation fails.
    """
    if req.limit is not None and req.limit <= 0:
        raise InvalidLimit("Employee sync limit must be positive")

    run_id = await _begin_sync(store, principal_id)
    created = 0
    updated = 0
    unchanged = 0

    total: int | None = None
    available = 0
    published = False
    interrupted = False
    failure_code = "employee.sync_failed"
    failure_reason = "Employee sync failed"
    updates = _stream_employee_source(source, req.limit)
    try:
        yield EmployeeSyncStarted(run_id)
        async for item in updates:
            total = item.total
            available = item.available
            match await _write_employee(store, run_id, item.employee):
                case EmployeeWriteOutcome.CREATED:
                    created += 1
                case EmployeeWriteOutcome.UPDATED:
                    updated += 1
                case EmployeeWriteOutcome.UNCHANGED:
                    unchanged += 1
            yield EmployeeSyncProgress(
                total=item.total,
                available=item.available,
                created=created,
                updated=updated,
                unchanged=unchanged,
                operation_id=run_id,
            )

        if created + updated + unchanged == 0:
            raise NoEmployeesFound("Employee sync found no employees")

        counts = await _publish_sync(store, run_id)
        published = True
        yield EmployeeSyncResult(
            total,
            available,
            counts.created,
            counts.updated,
            counts.unchanged,
            run_id,
        )
    except BaseException as exc:
        interrupted = isinstance(exc, anyio.get_cancelled_exc_class() | GeneratorExit)
        if interrupted:
            failure_code = "request_interrupted"
            failure_reason = "Employee sync was interrupted"
        elif isinstance(exc, NoEmployeesFound):
            failure_code = "employee.no_employees"
            failure_reason = "No employees were found"
        elif isinstance(exc, EmployeeSourceUnavailable):
            failure_code = "employee.source_unavailable"
            failure_reason = "Employee source is unavailable"
        elif isinstance(exc, EmployeeStoreUnavailable):
            failure_code = "employee.store_unavailable"
            failure_reason = "Employee store is unavailable"
        raise
    finally:
        with anyio.CancelScope(shield=True):
            try:
                await updates.aclose()
            finally:
                if not published:
                    await _discard_sync(
                        store,
                        run_id,
                        interrupted=interrupted,
                        failure_code=failure_code,
                        summary={
                            "processed": created + updated + unchanged,
                            "total": total,
                            "available": available,
                            "created": created,
                            "updated": updated,
                            "unchanged": unchanged,
                            "published": False,
                            "reason": failure_reason,
                        },
                    )
