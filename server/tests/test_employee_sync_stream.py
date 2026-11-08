"""Verify the employee sync HTTP response is a JSON Lines stream."""

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.employees.employee_sync_route import router
from dependencies import get_employee_source, get_employee_store
from domain.models.employee import Employee


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

    async def save_employee(self, employee: Employee) -> None:
        self.saved.append(employee)


def test_sync_streams_progress_and_completion_as_json_lines() -> None:
    app = FastAPI()
    app.include_router(router)
    store = Store()

    async def source() -> Source:
        return Source([Employee(id="one"), Employee(id="two")])

    async def employee_store() -> Store:
        return store

    app.dependency_overrides[get_employee_source] = source
    app.dependency_overrides[get_employee_store] = employee_store

    async def request() -> tuple[str, list[dict[str, object]]]:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await asyncio.wait_for(
                client.post("/api/v1/employees/sync", json={"limit": 1}), timeout=5
            )
        assert response.status_code == 200
        return response.headers["content-type"], [
            json.loads(line) for line in response.text.splitlines()
        ]

    content_type, events = asyncio.run(request())
    assert content_type == "application/jsonl"
    assert events == [
        {"status": "progress", "processed": 1, "total": 1, "available": 2},
        {"status": "complete", "processed": 1, "total": 1, "available": 2},
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

    async def request() -> list[dict[str, object]]:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await asyncio.wait_for(
                client.post("/api/v1/employees/sync", json={}), timeout=5
            )
        assert response.status_code == 200
        return [json.loads(line) for line in response.text.splitlines()]

    assert asyncio.run(request()) == [
        {
            "status": "error",
            "processed": 0,
            "available": 0,
            "error_status": 404,
            "detail": "Employee sync found no employees",
        }
    ]
