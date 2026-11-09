"""Verify employee sync saves and reports each profile as it arrives."""

import asyncio
from collections.abc import AsyncIterator

from domain.models.employee import Employee, SyncEmployeesRequest
from domain.ports.persistence.employee_store import SaveEmployeeOutcome
from domain.use_cases.employee_sync import sync_employees


def test_sync_saves_and_reports_before_fetching_next_profile() -> None:
    events: list[str] = []

    class Source:
        async def stream_employees(
            self, limit: int | None
        ) -> AsyncIterator[tuple[Employee, int, int]]:
            assert limit is None
            for member_id in ("one", "two"):
                events.append(f"fetched:{member_id}")
                yield Employee(id=member_id), 2, 2

    class Store:
        async def save_employee(self, employee: Employee) -> SaveEmployeeOutcome:
            events.append(f"saved:{employee.id}")
            return SaveEmployeeOutcome.CREATED

    async def run() -> None:
        async for update in sync_employees(SyncEmployeesRequest(), Source(), Store()):
            events.append(f"progress:{update.processed}")

    asyncio.run(run())

    assert events == [
        "fetched:one",
        "saved:one",
        "progress:1",
        "fetched:two",
        "saved:two",
        "progress:2",
    ]
