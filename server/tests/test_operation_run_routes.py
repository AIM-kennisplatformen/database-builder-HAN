import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from api.routes.operations import get_operation_run
from api.routes.operations import router as operations_router
from domain.ports.operation_run_store import OperationRun


class OperationRunReader:
    def __init__(self, run: OperationRun) -> None:
        self.run: OperationRun = run
        self.lookups: list[tuple[UUID, str]] = []

    async def get_operation_run(
        self, operation_id: UUID, principal_id: str
    ) -> OperationRun | None:
        self.lookups.append((operation_id, principal_id))
        if operation_id != self.run.id or principal_id != "operator":
            return None
        return self.run


def test_get_operation_run_returns_terminal_metadata_and_domain_links() -> None:
    operation_id = uuid4()
    started_at = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
    finished_at = datetime(2026, 11, 20, 10, 1, tzinfo=UTC)
    run = OperationRun(
        id=operation_id,
        kind="metadata.ingest",
        status="complete",
        started_at=started_at,
        finished_at=finished_at,
        summary={"processed": 7, "skipped_drive_ids": ["restricted-drive"]},
        failure_code=None,
        domain_ids={"group_id": "group", "drive_run_ids": ["drive-run-1"]},
    )
    reader = OperationRunReader(run)
    response = asyncio.run(get_operation_run(operation_id, "operator", reader))

    assert response.model_dump() == {
        "id": operation_id,
        "kind": "metadata.ingest",
        "status": "complete",
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "summary": {"processed": 7, "skipped_drive_ids": ["restricted-drive"]},
        "failure_code": None,
        "domain_ids": {"group_id": "group", "drive_run_ids": ["drive-run-1"]},
    }
    assert reader.lookups == [(operation_id, "operator")]


def test_get_operation_run_hides_runs_owned_by_another_principal() -> None:
    operation_id = uuid4()
    run = OperationRun(
        id=operation_id,
        kind="employee.sync",
        status="interrupted",
        started_at=datetime(2026, 11, 20, 10, 0, tzinfo=UTC),
        finished_at=datetime(2026, 11, 20, 10, 1, tzinfo=UTC),
        summary={},
        failure_code="request_interrupted",
        domain_ids={},
    )
    reader = OperationRunReader(run)
    with pytest.raises(HTTPException) as error:
        asyncio.run(get_operation_run(operation_id, "someone-else", reader))

    assert error.value.status_code == 404
    assert reader.lookups == [(operation_id, "someone-else")]


def test_get_operation_run_exposes_failure_state_and_safe_code() -> None:
    operation_id = uuid4()
    reader = OperationRunReader(
        OperationRun(
            id=operation_id,
            kind="employee.sync",
            status="failed",
            started_at=datetime(2026, 11, 20, 10, 0, tzinfo=UTC),
            finished_at=datetime(2026, 11, 20, 10, 1, tzinfo=UTC),
            summary={"processed": 2},
            failure_code="employee.source_unavailable",
            domain_ids={},
        )
    )

    response = asyncio.run(get_operation_run(operation_id, "operator", reader))

    assert response.status == "failed"
    assert response.failure_code == "employee.source_unavailable"
    assert response.summary == {"processed": 2}


def test_operation_run_route_is_registered_as_get_by_uuid() -> None:
    reader = OperationRunReader(
        OperationRun(
            id=uuid4(),
            kind="employee.sync",
            status="failed",
            started_at=datetime(2026, 11, 20, 10, 0, tzinfo=UTC),
            finished_at=None,
            summary={},
            failure_code="employee.source_unavailable",
            domain_ids={},
        )
    )
    route = next(
        route
        for route in operations_router.routes
        if isinstance(route, APIRoute)
        and route.path == "/api/v1/operations/{operation_id}"
    )
    assert route.methods is not None and "GET" in route.methods
    assert reader.lookups == []
