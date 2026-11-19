import asyncio
from collections.abc import AsyncIterator

import pytest

from api.routes.employees import sync as employee_sync_route
from api.routes.employees.sync import EmployeesSyncRequestBody, sync_employees_endpoint
from domain.exceptions.employee_sync_errors import SourceUnavailable
from domain.models.employee import Employee
from domain.ports.employees.employee_source import EmployeeStreamItem
from domain.ports.employees.employee_store import EmployeeWriter, WriteEmployeeOutcome
from domain.use_cases.employee_sync import (
    EmployeesSyncProgress,
    EmployeesSyncRequest,
    EmployeesSyncResult,
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
    async def write_employee(self, employee: Employee) -> WriteEmployeeOutcome:
        _ = employee
        return WriteEmployeeOutcome.CREATED


def test_employee_sync_yields_success_result_after_progress() -> None:
    async def collect():
        return [
            update
            async for update in sync_employees(
                EmployeesSyncRequest(), EmployeeSource(), EmployeeStore()
            )
        ]

    updates = asyncio.run(collect())
    assert isinstance(updates[0], EmployeesSyncProgress)
    assert isinstance(updates[-1], EmployeesSyncResult)
    assert updates[-1].processed == 3
    assert updates[-1].total == 3


def test_employee_sync_emits_rate_limited_progress_then_one_complete() -> None:
    async def collect():
        return [
            event
            async for event in sync_employees_endpoint(
                EmployeesSyncRequestBody(), EmployeeSource(), EmployeeStore()
            )
        ]

    events = asyncio.run(collect())

    # The first progress event passes; subsequent updates arrive inside the
    # limiter interval. Completion is a terminal event and is not rate limited.
    assert [event.event for event in events] == ["progress", "complete"]
    assert events[-1].data == {
        "status": "complete",
        "processed": 3,
        "total": 3,
        "available": 3,
        "created": 3,
        "updated": 0,
        "unchanged": 0,
    }


def test_employee_sync_emits_one_error_terminal_after_source_failure() -> None:
    async def collect():
        return [
            event
            async for event in sync_employees_endpoint(
                EmployeesSyncRequestBody(),
                EmployeeSource(fail_after=1),
                EmployeeStore(),
            )
        ]

    events = asyncio.run(collect())

    assert [event.event for event in events] == ["progress", "error"]
    assert sum(event.event in {"complete", "error"} for event in events) == 1
    assert events[-1].data == {
        "status": "error",
        "processed": 1,
        "total": 3,
        "available": 3,
        "created": 1,
        "updated": 0,
        "unchanged": 0,
        "error_status": 501,
        "detail": "Employee source is unavailable",
    }
    assert "traceback" not in events[-1].data


def test_employee_sync_emits_generic_unexpected_error_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken_sync(
        request: EmployeesSyncRequest,
        source: EmployeeSource,
        store: EmployeeWriter,
    ) -> AsyncIterator[EmployeesSyncProgress | EmployeesSyncResult]:
        _ = (request, source, store)
        yield EmployeesSyncProgress(
            total=3, available=5, created=1, updated=0, unchanged=0
        )
        raise RuntimeError("unexpected backend failure")

    monkeypatch.setattr(employee_sync_route, "sync_employees", broken_sync)

    async def collect():
        return [
            event
            async for event in sync_employees_endpoint(
                EmployeesSyncRequestBody(), EmployeeSource(), EmployeeStore()
            )
        ]

    events = asyncio.run(collect())

    assert [event.event for event in events] == ["progress", "error"]
    assert events[-1].data == {
        "status": "error",
        "processed": 1,
        "total": 3,
        "available": 5,
        "created": 1,
        "updated": 0,
        "unchanged": 0,
        "error_status": 500,
        "detail": "Employee sync failed",
    }
    assert "traceback" not in events[-1].data


def test_employee_sync_subclass_inherits_known_error_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SpecializedSourceUnavailable(SourceUnavailable):
        pass

    async def broken_sync(
        request: EmployeesSyncRequest,
        source: EmployeeSource,
        store: EmployeeWriter,
    ) -> AsyncIterator[EmployeesSyncProgress | EmployeesSyncResult]:
        _ = (request, source, store)
        yield EmployeesSyncProgress(
            total=3, available=5, created=1, updated=0, unchanged=0
        )
        raise SpecializedSourceUnavailable("employee source unavailable")

    monkeypatch.setattr(employee_sync_route, "sync_employees", broken_sync)

    async def collect():
        return [
            event
            async for event in sync_employees_endpoint(
                EmployeesSyncRequestBody(), EmployeeSource(), EmployeeStore()
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == ["progress", "error"]
    assert events[-1].data["error_status"] == 501
    assert "traceback" not in events[-1].data
