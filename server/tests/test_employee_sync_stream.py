"""Verify the employee sync HTTP response uses Server-Sent Events."""

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from api.routes.employees.router import router
from dependencies import get_employee_source, get_employee_store
from domain.models.employee import Employee
from domain.ports.persistence.employee_store import WriteEmployeeOutcome


class Source:
    def __init__(self, employees: list[Employee]) -> None:
        self.employees: list[Employee] = employees

    async def stream_employees(
        self, limit: int | None
    ) -> AsyncIterator[tuple[Employee, int, int]]:
        available = len(self.employees)
        total = min(available, limit) if limit is not None else available
        for employee in self.employees[:limit]:
            yield employee, total, available


class Store:
    def __init__(self) -> None:
        self.saved: list[Employee] = []

    async def write_employee(self, employee: Employee) -> WriteEmployeeOutcome:
        self.saved.append(employee)
        return WriteEmployeeOutcome.CREATED


def parse_sse(response: Response) -> list[tuple[str, dict[str, object]]]:
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.endswith("\n\n")
    events: list[tuple[str, dict[str, object]]] = []
    for block in response.text.removesuffix("\n\n").split("\n\n"):
        event_line, data_line = block.splitlines()
        assert event_line.startswith("event: ")
        assert data_line.startswith("data: ")
        events.append((event_line.removeprefix("event: "), json.loads(data_line[6:])))
    return events


def test_sync_streams_progress_and_completion_as_sse() -> None:
    app = FastAPI()
    app.include_router(router)
    store = Store()

    async def source() -> Source:
        return Source([Employee(id="one"), Employee(id="two")])

    async def employee_store() -> Store:
        return store

    app.dependency_overrides[get_employee_source] = source
    app.dependency_overrides[get_employee_store] = employee_store

    async def request() -> list[tuple[str, dict[str, object]]]:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await asyncio.wait_for(
                client.post("/api/v1/employees/sync", json={"limit": 1}), timeout=5
            )
        assert response.status_code == 200
        return parse_sse(response)

    assert asyncio.run(request()) == [
        (
            "progress",
            {
                "status": "progress",
                "processed": 1,
                "total": 1,
                "available": 2,
                "created": 1,
                "updated": 0,
                "unchanged": 0,
            },
        ),
        (
            "complete",
            {
                "status": "complete",
                "processed": 1,
                "total": 1,
                "available": 2,
                "created": 1,
                "updated": 0,
                "unchanged": 0,
            },
        ),
    ]
    assert store.saved == [Employee(id="one")]


def test_sync_streams_error_record_when_source_is_empty() -> None:
    app = FastAPI()
    app.include_router(router)

    async def source() -> Source:
        return Source([])

    async def employee_store() -> Store:
        return Store()

    app.dependency_overrides[get_employee_source] = source
    app.dependency_overrides[get_employee_store] = employee_store

    async def request() -> list[tuple[str, dict[str, object]]]:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await asyncio.wait_for(
                client.post("/api/v1/employees/sync", json={}), timeout=5
            )
        assert response.status_code == 200
        return parse_sse(response)

    assert asyncio.run(request()) == [
        (
            "error",
            {
                "status": "error",
                "processed": 0,
                "available": 0,
                "created": 0,
                "updated": 0,
                "unchanged": 0,
                "error_status": 404,
                "detail": "Employee sync found no employees",
            },
        )
    ]
