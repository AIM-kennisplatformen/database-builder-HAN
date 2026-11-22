import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import override
from uuid import UUID, uuid4

import pytest

from api.routes.employees import sync as employee_sync_route
from api.routes.employees.sync import EmployeesSyncRequestBody, sync_employees_endpoint
from api.routes.operations import get_operation_run
from domain.exceptions.employee_sync_errors import EmployeeSourceUnavailable
from domain.models.employee import Employee
from domain.ports.employees.employee_source import EmployeeStreamItem
from domain.ports.employees.employee_store import (
    EmployeeSyncCounts,
    EmployeeWriteOutcome,
    EmployeeWriter,
)
from domain.ports.operation_run_store import OperationRun
from domain.use_cases.employee_sync import (
    EmployeeSyncProgress,
    EmployeeSyncRequest,
    EmployeeSyncResult,
    EmployeeSyncStarted,
    sync_employees,
)


class EmployeeSource:
    def __init__(self, fail_after: int | None = None) -> None:
        self.fail_after: int | None = fail_after

    async def stream_employees(
        self, limit: int | None
    ) -> AsyncIterator[EmployeeStreamItem]:
        assert limit is None
        for index in range(3):
            if self.fail_after == index:
                raise RuntimeError("source unavailable")
            yield EmployeeStreamItem(Employee(str(index)), total=3, available=3)


class EmployeeStore:
    def __init__(self) -> None:
        self.committed: list[Employee] = []
        self.staged: dict[UUID, list[Employee]] = {}
        self.statuses: dict[UUID, str] = {}
        self.summaries: dict[UUID, dict[str, object]] = {}
        self.failure_codes: dict[UUID, str | None] = {}
        self.owners: dict[UUID, str] = {}
        self.started_at: dict[UUID, datetime] = {}

    async def begin_sync(self, principal_id: str) -> UUID:
        assert principal_id == "operator"
        run_id = uuid4()
        self.staged[run_id] = []
        self.statuses[run_id] = "running"
        self.owners[run_id] = principal_id
        self.started_at[run_id] = datetime.now(UTC)
        return run_id

    async def stage_employee(
        self, run_id: UUID, employee: Employee
    ) -> EmployeeWriteOutcome:
        self.staged[run_id].append(employee)
        return EmployeeWriteOutcome.CREATED

    async def publish_sync(self, run_id: UUID) -> EmployeeSyncCounts:
        employees = self.staged.pop(run_id)
        self.committed.extend(employees)
        self.statuses[run_id] = "complete"
        self.summaries[run_id] = {
            "processed": len(employees),
            "created": len(employees),
            "updated": 0,
            "unchanged": 0,
            "published": True,
        }
        self.failure_codes[run_id] = None
        return EmployeeSyncCounts(len(employees), 0, 0)

    async def discard_sync(
        self,
        run_id: UUID,
        *,
        interrupted: bool = False,
        failure_code: str = "employee.sync_failed",
        summary: dict[str, object] | None = None,
    ) -> None:
        self.staged.pop(run_id, None)
        self.statuses[run_id] = "interrupted" if interrupted else "failed"
        self.summaries[run_id] = summary or {}
        self.failure_codes[run_id] = failure_code

    async def get_operation_run(
        self, operation_id: UUID, principal_id: str
    ) -> OperationRun | None:
        if (
            operation_id not in self.statuses
            or self.owners[operation_id] != principal_id
        ):
            return None
        return OperationRun(
            id=operation_id,
            kind="employee.sync",
            status=self.statuses[operation_id],
            started_at=self.started_at[operation_id],
            finished_at=datetime.now(UTC)
            if self.statuses[operation_id] != "running"
            else None,
            summary=self.summaries.get(operation_id, {}),
            failure_code=self.failure_codes.get(operation_id),
            domain_ids={},
        )


def test_employee_sync_yields_success_result_after_progress() -> None:
    store = EmployeeStore()

    async def collect():
        return [
            update
            async for update in sync_employees(
                EmployeeSyncRequest(), EmployeeSource(), store, "operator"
            )
        ]

    updates = asyncio.run(collect())
    assert isinstance(updates[0], EmployeeSyncStarted)
    assert isinstance(updates[1], EmployeeSyncProgress)
    assert isinstance(updates[-1], EmployeeSyncResult)
    assert updates[-1].processed == 3
    assert updates[-1].total == 3
    assert len(store.committed) == 3
    assert store.staged == {}
    assert store.statuses[updates[0].operation_id] == "complete"


def test_employee_sync_failure_discards_all_staged_employees() -> None:
    store = EmployeeStore()

    async def collect() -> None:
        async for _ in sync_employees(
            EmployeeSyncRequest(), EmployeeSource(fail_after=1), store, "operator"
        ):
            pass

    with pytest.raises(EmployeeSourceUnavailable):
        asyncio.run(collect())

    assert store.committed == []
    assert store.staged == {}
    assert set(store.statuses.values()) == {"failed"}


def test_employee_sync_cancellation_discards_all_staged_employees() -> None:
    store = EmployeeStore()
    source = EmployeeSource()

    async def cancel_after_progress() -> None:
        updates = sync_employees(EmployeeSyncRequest(), source, store, "operator")
        started = await anext(updates)
        assert isinstance(started, EmployeeSyncStarted)
        first = await anext(updates)
        assert isinstance(first, EmployeeSyncProgress)
        assert store.committed == []
        assert sum(map(len, store.staged.values())) == 1
        await updates.aclose()

    asyncio.run(cancel_after_progress())

    assert store.committed == []
    assert store.staged == {}
    assert set(store.statuses.values()) == {"interrupted"}


def test_employee_sync_cancellation_closes_source_iterator() -> None:
    class CloseTrackingSource(EmployeeSource):
        def __init__(self) -> None:
            super().__init__()
            self.closed: bool = False

        @override
        async def stream_employees(
            self, limit: int | None
        ) -> AsyncIterator[EmployeeStreamItem]:
            assert limit is None
            try:
                yield EmployeeStreamItem(Employee("0"), total=3, available=3)
                await asyncio.Event().wait()
            finally:
                self.closed = True

    store, source = EmployeeStore(), CloseTrackingSource()

    async def cancel_after_progress() -> None:
        updates = sync_employees(EmployeeSyncRequest(), source, store, "operator")
        started = await anext(updates)
        assert isinstance(started, EmployeeSyncStarted)
        first = await anext(updates)
        assert isinstance(first, EmployeeSyncProgress)
        await updates.aclose()

    asyncio.run(cancel_after_progress())

    assert source.closed
    assert store.committed == []
    assert store.staged == {}


def test_employee_sync_emits_rate_limited_progress_then_one_complete() -> None:
    async def collect():
        return [
            event
            async for event in sync_employees_endpoint(
                EmployeesSyncRequestBody(),
                "operator",
                EmployeeSource(),
                EmployeeStore(),
            )
        ]

    events = asyncio.run(collect())

    # The first progress event passes; subsequent updates arrive inside the
    # limiter interval. Completion is a terminal event and is not rate limited.
    assert [event.event for event in events] == ["started", "progress", "complete"]
    assert events[0].data["operation_id"]
    assert events[-1].data == {
        "status": "complete",
        "processed": 3,
        "total": 3,
        "available": 3,
        "created": 3,
        "updated": 0,
        "unchanged": 0,
        "operation_id": events[0].data["operation_id"],
    }


def test_employee_sync_emits_one_error_terminal_after_source_failure() -> None:
    store = EmployeeStore()

    async def collect():
        return [
            event
            async for event in sync_employees_endpoint(
                EmployeesSyncRequestBody(),
                "operator",
                EmployeeSource(fail_after=1),
                store,
            )
        ]

    events = asyncio.run(collect())

    assert [event.event for event in events] == ["started", "progress", "error"]
    assert sum(event.event in {"complete", "error"} for event in events) == 1
    assert events[-1].data == {
        "status": "error",
        "operation_id": events[0].data["operation_id"],
        "error": {
            "code": "employee.source_unavailable",
            "http_status": 501,
            "detail": "Employee source is unavailable",
        },
        "context": {
            "operation": "employee.sync",
            "operation_id": events[0].data["operation_id"],
        },
        "progress": {
            "processed": 1,
            "total": 3,
            "available": 3,
            "created": 1,
            "updated": 0,
            "unchanged": 0,
        },
    }
    assert "traceback" not in events[-1].data
    operation_id = UUID(events[-1].data["operation_id"])
    status = asyncio.run(get_operation_run(operation_id, "operator", store))
    assert status.status == "failed"
    assert status.failure_code == "employee.source_unavailable"
    assert status.summary == {
        "processed": 1,
        "total": 3,
        "available": 3,
        "created": 1,
        "updated": 0,
        "unchanged": 0,
        "published": False,
        "reason": "Employee source is unavailable",
    }
    assert "source unavailable" not in str(status.summary)


def test_employee_sync_emits_generic_unexpected_error_payload(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def broken_sync(
        request: EmployeeSyncRequest,
        source: EmployeeSource,
        store: EmployeeWriter,
        principal_id: str,
    ) -> AsyncIterator[EmployeeSyncProgress | EmployeeSyncResult]:
        _ = (request, source, store, principal_id)
        yield EmployeeSyncProgress(
            total=3,
            available=5,
            created=1,
            updated=0,
            unchanged=0,
            operation_id=uuid4(),
        )
        raise RuntimeError("unexpected backend failure")

    monkeypatch.setattr(employee_sync_route, "sync_employees", broken_sync)

    async def collect():
        return [
            event
            async for event in sync_employees_endpoint(
                EmployeesSyncRequestBody(),
                "operator",
                EmployeeSource(),
                EmployeeStore(),
            )
        ]

    events = asyncio.run(collect())

    assert [event.event for event in events] == ["progress", "error"]
    assert events[-1].data == {
        "status": "error",
        "operation_id": None,
        "error": {
            "code": "internal_error",
            "http_status": 500,
            "detail": "The operation failed",
        },
        "context": {"operation": "employee.sync", "operation_id": None},
        "progress": {
            "processed": 1,
            "total": 3,
            "available": 5,
            "created": 1,
            "updated": 0,
            "unchanged": 0,
        },
    }
    assert "traceback" not in events[-1].data
    assert "unexpected backend failure" not in str(events[-1].data)
    failure_records = [record for record in caplog.records if record.exc_info]
    assert len(failure_records) == 1
    assert failure_records[0].exc_info is not None
    assert isinstance(failure_records[0].exc_info[1], RuntimeError)
    assert sum(record.name == "api.errors" for record in caplog.records) == 1
    assert "unexpected backend failure" in caplog.text


def test_employee_sync_subclass_inherits_known_error_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SpecializedSourceUnavailable(EmployeeSourceUnavailable):
        pass

    async def broken_sync(
        request: EmployeeSyncRequest,
        source: EmployeeSource,
        store: EmployeeWriter,
        principal_id: str,
    ) -> AsyncIterator[EmployeeSyncProgress | EmployeeSyncResult]:
        _ = (request, source, store, principal_id)
        yield EmployeeSyncProgress(
            total=3,
            available=5,
            created=1,
            updated=0,
            unchanged=0,
            operation_id=uuid4(),
        )
        raise SpecializedSourceUnavailable("employee source unavailable")

    monkeypatch.setattr(employee_sync_route, "sync_employees", broken_sync)

    async def collect():
        return [
            event
            async for event in sync_employees_endpoint(
                EmployeesSyncRequestBody(),
                "operator",
                EmployeeSource(),
                EmployeeStore(),
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == ["progress", "error"]
    assert events[-1].data["error"]["code"] == "employee.source_unavailable"
    assert events[-1].data["error"]["http_status"] == 501
    assert "traceback" not in events[-1].data
