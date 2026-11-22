import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from psycopg_pool import AsyncConnectionPool

from domain.models.ubwfris import IbRow, RhaRow
from infrastructure.postgres.ubwfris_writer import (
    PostgresIbWriter,
    PostgresRhaWriter,
    interrupt_ubwfris_snapshots,
)


@asynccontextmanager
async def _async_resource(value: object) -> AsyncGenerator[object]:
    yield value


def _ib_row() -> IbRow:
    return IbRow(
        row_number=1,
        project_id="project",
        project_name="Project",
        employee_name="Employee",
        budgeted_hours=Decimal(10),
        projected_hours=Decimal(8),
        booked_hours=Decimal(4),
        period=202601,
        remaining_hours=Decimal(6),
        cost_center="center",
    )


def _rha_row() -> RhaRow:
    return RhaRow(
        row_number=1,
        employee_number="employee",
        employee_name="Employee",
        project_manager="Manager",
        project_id="project",
        project_status="active",
        project_name="Project",
        subproject_id="subproject",
        subproject_status="active",
        subproject_name="Subproject",
        cost_center="center",
        registration_date=date(2026, 1, 1),
        hours=Decimal(8),
        project_type="type",
        parttime_percentage=None,
        main_department="department",
        academy="academy",
        main_department_name="Department",
        employment_cost_center="employment-center",
    )


@pytest.mark.parametrize(
    "row_type",
    ["ib", "rha"],
)
def test_complete_snapshot_marks_running_snapshot_complete(
    row_type: str,
) -> None:
    running = MagicMock()
    running.fetchone = AsyncMock(return_value=("running",))
    completed = MagicMock()
    completed.fetchone = AsyncMock(return_value=("snapshot",))
    operation_completed = MagicMock()
    operation_completed.fetchone = AsyncMock(return_value=("operation",))
    connection = MagicMock()
    connection.execute = AsyncMock(
        side_effect=[running, None, None, completed, operation_completed]
    )
    pool = MagicMock()
    pool.connection.return_value.__aenter__.return_value = connection

    if row_type == "ib":
        writer = PostgresIbWriter(cast(AsyncConnectionPool, pool))
    else:
        writer = PostgresRhaWriter(cast(AsyncConnectionPool, pool))
    asyncio.run(writer.publish_snapshot("snapshot", uuid4(), {"created_rows": 1}))

    statements = [args.args[0] for args in connection.execute.await_args_list]
    assert "SELECT status FROM ubwfris_snapshots" in statements[0]
    assert "FOR UPDATE" in statements[0]
    assert f"INSERT INTO ubwfris_{row_type}_rows (" in statements[1]
    assert f"FROM ubwfris_{row_type}_rows_staging" in statements[1]
    assert f"DELETE FROM ubwfris_{row_type}_rows_staging" in statements[2]
    assert "UPDATE ubwfris_snapshots SET status = 'complete'" in statements[3]
    assert "UPDATE operation_runs SET status = 'complete'" in statements[4]
    assert connection.transaction.called
    running.fetchone.assert_awaited_once_with()
    completed.fetchone.assert_awaited_once_with()
    operation_completed.fetchone.assert_awaited_once_with()


@pytest.mark.parametrize("row_type", ["ib", "rha"])
def test_row_writes_copy_to_staging_tables(row_type: str) -> None:
    copy = MagicMock()
    copy.write_row = AsyncMock()
    cursor = MagicMock()

    def copy_context(query: str) -> object:
        _ = query
        return _async_resource(copy)

    cursor.copy = MagicMock(side_effect=copy_context)
    connection = MagicMock()
    connection.cursor = MagicMock(side_effect=lambda: _async_resource(cursor))
    connection.transaction = MagicMock(side_effect=lambda: _async_resource(None))
    pool = MagicMock()
    pool.connection = MagicMock(side_effect=lambda: _async_resource(connection))

    if row_type == "ib":
        writer = PostgresIbWriter(cast(AsyncConnectionPool, pool))
        result = asyncio.run(writer.stage_ubwfris_rows("snapshot", [_ib_row()]))
    else:
        writer = PostgresRhaWriter(cast(AsyncConnectionPool, pool))
        result = asyncio.run(writer.stage_ubwfris_rows("snapshot", [_rha_row()]))

    query = cursor.copy.call_args.args[0]
    assert f"COPY ubwfris_{row_type}_rows_staging" in query
    assert f"COPY ubwfris_{row_type}_rows (" not in query
    assert result == 1
    copy.write_row.assert_awaited_once()


def test_startup_recovery_deletes_only_running_snapshots() -> None:
    connection = MagicMock()
    connection.execute = AsyncMock()
    pool = MagicMock()
    pool.connection.return_value.__aenter__.return_value = connection

    asyncio.run(interrupt_ubwfris_snapshots(cast(AsyncConnectionPool, pool)))

    statements = [args.args[0] for args in connection.execute.await_args_list]
    assert len(statements) == 4
    assert "DELETE FROM ubwfris_ib_rows_staging" in statements[0]
    assert "snapshot.status = 'running'" in statements[0]
    assert "DELETE FROM ubwfris_rha_rows_staging" in statements[1]
    assert "snapshot.status = 'running'" in statements[1]
    assert "DELETE FROM ubwfris_snapshots WHERE status = 'running'" in statements[2]
    assert "UPDATE operation_runs SET status = 'interrupted'" in statements[3]
    assert all("DELETE FROM ubwfris_ib_rows WHERE" not in sql for sql in statements)
    assert all("DELETE FROM ubwfris_rha_rows WHERE" not in sql for sql in statements)


def test_discard_preserves_completed_snapshot() -> None:
    cursor = MagicMock()
    cursor.fetchone = AsyncMock(return_value=("complete",))
    connection = MagicMock()
    connection.execute = AsyncMock(return_value=cursor)
    pool = MagicMock()
    pool.connection.return_value.__aenter__.return_value = connection

    asyncio.run(
        PostgresIbWriter(cast(AsyncConnectionPool, pool)).discard_snapshot("snapshot")
    )

    assert connection.execute.await_count == 1
    sql, values = connection.execute.await_args.args
    assert "SELECT status FROM ubwfris_snapshots" in sql
    assert values == ("snapshot", "ib")
