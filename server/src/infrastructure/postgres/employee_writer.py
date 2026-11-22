"""Stage Graph employee syncs and publish each run atomically in PostgreSQL."""

from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime
from json import dumps
from typing import cast, override
from uuid import UUID, uuid4

import anyio
from psycopg import AsyncConnection, AsyncCursor
from psycopg_pool import AsyncConnectionPool

from domain.models.employee import Employee
from domain.ports.employees.employee_store import (
    EmployeeSyncCounts,
    EmployeeWriteOutcome,
    EmployeeWriter,
)

_EMPLOYEE_FIELDS = (
    "display_name",
    "given_name",
    "surname",
    "mail",
    "company_name",
    "department",
    "job_title",
    "skills",
    "responsibilities",
    "interests",
)
_EMPLOYEE_COLUMNS = ", ".join(_EMPLOYEE_FIELDS)
_EMPLOYEE_PLACEHOLDERS = ", ".join("%s" for _ in _EMPLOYEE_FIELDS)
_EMPLOYEE_UPDATE = ", ".join(
    f"{field} = EXCLUDED.{field}" for field in _EMPLOYEE_FIELDS
)
_EMPLOYEE_RESET_LOCK = 7368369943385156102


@asynccontextmanager
async def _employee_transaction(
    pool: AsyncConnectionPool,
) -> AsyncGenerator[tuple[AsyncConnection, AsyncCursor]]:
    """Keep SQL cancellable and shield cursor/transaction/pool teardown."""
    stack = AsyncExitStack()
    try:
        connection = await stack.enter_async_context(pool.connection())
        _ = await stack.enter_async_context(connection.transaction())
        cursor = await stack.enter_async_context(connection.cursor())
    except BaseException as exc:
        with anyio.CancelScope(shield=True):
            _ = await stack.__aexit__(type(exc), exc, exc.__traceback__)
        raise

    try:
        yield connection, cursor
        await anyio.lowlevel.checkpoint()
    except BaseException as exc:
        with anyio.CancelScope(shield=True):
            _ = await stack.__aexit__(type(exc), exc, exc.__traceback__)
        raise
    else:
        with anyio.CancelScope(shield=True):
            _ = await stack.__aexit__(None, None, None)


def _extract_employee_values(employee: Employee) -> tuple[object, ...]:
    employment = employee.employment_details
    details = employee.self_reported_details
    return (
        employee.display_name,
        employee.given_name,
        employee.surname,
        employee.mail,
        employment.company_name if employment else None,
        employment.department if employment else None,
        employment.job_title if employment else None,
        details.skills if details else None,
        details.responsibilities if details else None,
        details.interests if details else None,
    )


async def _write_employee_values(
    cursor: AsyncCursor, employee_id: str, values: tuple[object, ...]
) -> EmployeeWriteOutcome:
    _ = await cursor.execute(
        f"""SELECT is_present, {_EMPLOYEE_COLUMNS}
            FROM user_ms_graph_sources
            WHERE id = %s AND upper_inf(valid_at)
            FOR UPDATE""",
        (employee_id,),
    )
    current = await cursor.fetchone()
    if current == (True, *values):
        return EmployeeWriteOutcome.UNCHANGED

    _ = await cursor.execute(
        """SELECT GREATEST(
               clock_timestamp(),
               COALESCE(
                   max(lower(valid_at)) + interval '1 microsecond',
                   '-infinity'::timestamptz
               )
           )
           FROM user_ms_graph_sources
           WHERE id = %s AND upper_inf(valid_at)""",
        (employee_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("PostgreSQL did not return a sync timestamp")
    at = cast(datetime, row[0])

    if current is not None:
        _ = await cursor.execute(
            """UPDATE user_ms_graph_sources
               SET valid_at = tstzrange(lower(valid_at), %s, '[)'),
                   updated_at = %s
               WHERE id = %s AND upper_inf(valid_at)""",
            (at, at, employee_id),
        )
    _ = await cursor.execute(
        f"""INSERT INTO user_ms_graph_sources (
               id, valid_at, is_present, {_EMPLOYEE_COLUMNS}
           ) VALUES (
               %s, tstzrange(%s, NULL, '[)'), true, {_EMPLOYEE_PLACEHOLDERS}
           )""",
        (employee_id, at, *values),
    )
    return (
        EmployeeWriteOutcome.CREATED
        if current is None
        else EmployeeWriteOutcome.UPDATED
    )


class EmployeeWriterPostgres(EmployeeWriter):
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool: AsyncConnectionPool = pool

    async def delete_all_employee_data(self) -> bool:
        async with _employee_transaction(self._pool) as (_, cursor):
            _ = await cursor.execute(
                "SELECT pg_advisory_xact_lock(%s)", (_EMPLOYEE_RESET_LOCK,)
            )
            _ = await cursor.execute(
                """SELECT id FROM employee_sync_runs
                   WHERE status = 'running' FOR UPDATE"""
            )
            if await cursor.fetchone() is not None:
                return False
            _ = await cursor.execute(
                """SELECT id FROM operation_runs
                   WHERE kind = 'employee.sync' AND status = 'running'
                   FOR UPDATE"""
            )
            if await cursor.fetchone() is not None:
                return False
            _ = await cursor.execute("SELECT EXISTS (SELECT 1 FROM user_overrides)")
            if await cursor.fetchone() != (False,):
                return False
            _ = await cursor.execute("DELETE FROM user_ms_graph_sources")
            _ = await cursor.execute("DELETE FROM employee_sync_staging")
            _ = await cursor.execute("DELETE FROM employee_sync_runs")
            _ = await cursor.execute(
                "DELETE FROM operation_runs WHERE kind = 'employee.sync'"
            )
            return True

    @override
    async def begin_sync(self, principal_id: str) -> UUID:
        run_id = uuid4()
        async with _employee_transaction(self._pool) as (connection, _):
            _ = await connection.execute(
                "SELECT pg_advisory_xact_lock(%s)", (_EMPLOYEE_RESET_LOCK,)
            )
            _ = await connection.execute(
                """INSERT INTO operation_runs
                   (id, principal_id, kind, status, domain_ids)
                   VALUES (%s, %s, 'employee.sync', 'running', %s::jsonb)""",
                (run_id, principal_id, dumps({"employee_sync_run_id": str(run_id)})),
            )
            _ = await connection.execute(
                "INSERT INTO employee_sync_runs (id, status) VALUES (%s, 'running')",
                (run_id,),
            )
        return run_id

    @override
    async def stage_employee(
        self, run_id: UUID, employee: Employee
    ) -> EmployeeWriteOutcome:
        values = _extract_employee_values(employee)
        async with _employee_transaction(self._pool) as (_, cursor):
            _ = await cursor.execute(
                "SELECT status FROM employee_sync_runs WHERE id = %s FOR UPDATE",
                (run_id,),
            )
            run = await cursor.fetchone()
            if run != ("running",):
                raise RuntimeError("Employee sync run is not active")

            _ = await cursor.execute(
                f"""SELECT is_present, {_EMPLOYEE_COLUMNS}
                    FROM user_ms_graph_sources
                    WHERE id = %s AND upper_inf(valid_at)""",
                (employee.id,),
            )
            current = await cursor.fetchone()
            if current is None:
                outcome = EmployeeWriteOutcome.CREATED
            elif current == (True, *values):
                outcome = EmployeeWriteOutcome.UNCHANGED
            else:
                outcome = EmployeeWriteOutcome.UPDATED

            _ = await cursor.execute(
                f"""INSERT INTO employee_sync_staging (
                       run_id, employee_id, {_EMPLOYEE_COLUMNS}
                   ) VALUES (
                       %s, %s, {_EMPLOYEE_PLACEHOLDERS}
                   )
                   ON CONFLICT (run_id, employee_id) DO UPDATE SET
                       {_EMPLOYEE_UPDATE}""",
                (run_id, employee.id, *values),
            )
            return outcome

    @override
    async def publish_sync(self, run_id: UUID) -> EmployeeSyncCounts:
        created = 0
        updated = 0
        unchanged = 0
        async with _employee_transaction(self._pool) as (_, cursor):
            _ = await cursor.execute(
                "SELECT status FROM employee_sync_runs WHERE id = %s FOR UPDATE",
                (run_id,),
            )
            run = await cursor.fetchone()
            if run != ("running",):
                raise RuntimeError("Employee sync run is not active")

            # Serialize publication so concurrent runs cannot overlap temporal rows.
            _ = await cursor.execute(
                "SELECT pg_advisory_xact_lock(7368369943385156101)"
            )
            _ = await cursor.execute(
                f"""SELECT employee_id, {_EMPLOYEE_COLUMNS}
                    FROM employee_sync_staging
                    WHERE run_id = %s
                    ORDER BY employee_id""",
                (run_id,),
            )
            rows = await cursor.fetchall()
            for row in rows:
                outcome = await _write_employee_values(
                    cursor,
                    cast(str, row[0]),
                    cast(tuple[object, ...], tuple(row[1:])),
                )
                match outcome:
                    case EmployeeWriteOutcome.CREATED:
                        created += 1
                    case EmployeeWriteOutcome.UPDATED:
                        updated += 1
                    case EmployeeWriteOutcome.UNCHANGED:
                        unchanged += 1

            _ = await cursor.execute(
                "DELETE FROM employee_sync_staging WHERE run_id = %s", (run_id,)
            )
            _ = await cursor.execute(
                "UPDATE employee_sync_runs SET status = 'complete', finished_at = now() WHERE id = %s",
                (run_id,),
            )
            _ = await cursor.execute(
                """UPDATE operation_runs SET status = 'complete', finished_at = now(),
                   summary = %s::jsonb, domain_ids = %s::jsonb
                   WHERE id = %s AND status = 'running'""",
                (
                    dumps(
                        {"created": created, "updated": updated, "unchanged": unchanged}
                    ),
                    dumps({"employee_sync_run_id": str(run_id)}),
                    run_id,
                ),
            )
        return EmployeeSyncCounts(created, updated, unchanged)

    @override
    async def discard_sync(
        self,
        run_id: UUID,
        *,
        interrupted: bool = False,
        failure_code: str = "employee.sync_failed",
        summary: dict[str, object] | None = None,
    ) -> None:
        status = "interrupted" if interrupted else "failed"
        code = "request_interrupted" if interrupted else failure_code
        with anyio.CancelScope(shield=True):
            async with _employee_transaction(self._pool) as (_, cursor):
                _ = await cursor.execute(
                    "DELETE FROM employee_sync_staging WHERE run_id = %s",
                    (run_id,),
                )
                _ = await cursor.execute(
                    """UPDATE operation_runs SET status = %s, finished_at = now(),
                       failure_code = %s, summary = COALESCE(%s::jsonb, summary)
                       WHERE id = %s AND status = 'running'""",
                    (
                        status,
                        code,
                        dumps(summary) if summary is not None else None,
                        run_id,
                    ),
                )
                _ = await cursor.execute(
                    """UPDATE employee_sync_runs
                       SET status = %s, finished_at = now()
                       WHERE id = %s AND status = 'running'""",
                    (status, run_id),
                )

    async def interrupt_employee_sync_runs(self) -> None:
        """Remove staged rows from runs orphaned by a process crash."""
        async with _employee_transaction(self._pool) as (_, cursor):
            _ = await cursor.execute(
                """DELETE FROM employee_sync_staging AS staging
                   USING employee_sync_runs AS run
                   WHERE staging.run_id = run.id AND run.status = 'running'"""
            )
            _ = await cursor.execute(
                """UPDATE employee_sync_runs SET status = 'interrupted',
                   finished_at = now() WHERE status = 'running'"""
            )
            _ = await cursor.execute(
                """UPDATE operation_runs SET status = 'interrupted', finished_at = now(),
                   failure_code = 'process_interrupted'
                   WHERE kind = 'employee.sync' AND status = 'running'"""
            )
