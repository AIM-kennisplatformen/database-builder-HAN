"""Persist IB and RHA source rows in separate UBW FRIS snapshot tables."""

from collections.abc import AsyncGenerator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Literal, LiteralString, override

import anyio
from psycopg import AsyncConnection, AsyncCopy
from psycopg.errors import UniqueViolation
from psycopg_pool import AsyncConnectionPool

from domain.exceptions.ubwfris_ingest_errors import DuplicateUpload
from domain.models.ubwfris import IbRow, RhaRow
from domain.ports.ubwfris.ubwfris_store import UbwfrisWriter

_PUBLISH_IB_SNAPSHOT: LiteralString = """
    INSERT INTO ubwfris_ib_rows (
        snapshot_id, row_number, project_id, project_name, employee_name,
        budgeted_hours, projected_hours, booked_hours, period,
        remaining_hours, cost_center
    )
    SELECT snapshot_id, row_number, project_id, project_name, employee_name,
           budgeted_hours, projected_hours, booked_hours, period,
           remaining_hours, cost_center
    FROM ubwfris_ib_rows_staging
    WHERE snapshot_id = %s
"""
_PUBLISH_RHA_SNAPSHOT: LiteralString = """
    INSERT INTO ubwfris_rha_rows (
        snapshot_id, row_number, employee_number, employee_name,
        project_manager, project_id, project_status, project_name,
        subproject_id, subproject_status, subproject_name, cost_center,
        registration_date, hours, project_type, parttime_percentage,
        main_department, academy, main_department_name,
        employment_cost_center
    )
    SELECT snapshot_id, row_number, employee_number, employee_name,
           project_manager, project_id, project_status, project_name,
           subproject_id, subproject_status, subproject_name, cost_center,
           registration_date, hours, project_type, parttime_percentage,
           main_department, academy, main_department_name,
           employment_cost_center
    FROM ubwfris_rha_rows_staging
    WHERE snapshot_id = %s
"""


@asynccontextmanager
async def _pool_transaction(
    pool: AsyncConnectionPool,
) -> AsyncGenerator[AsyncConnection[tuple[object, ...]]]:
    """Keep transaction SQL cancellable and shield commit/rollback teardown."""
    stack = AsyncExitStack()
    try:
        connection = await stack.enter_async_context(pool.connection())
        _ = await stack.enter_async_context(connection.transaction())
    except BaseException as exc:
        with anyio.CancelScope(shield=True):
            _ = await stack.__aexit__(type(exc), exc, exc.__traceback__)
        raise

    try:
        yield connection
        await anyio.lowlevel.checkpoint()
    except BaseException as exc:
        with anyio.CancelScope(shield=True):
            _ = await stack.__aexit__(type(exc), exc, exc.__traceback__)
        raise
    else:
        with anyio.CancelScope(shield=True):
            _ = await stack.__aexit__(None, None, None)


@asynccontextmanager
async def _copy_transaction(
    pool: AsyncConnectionPool, query: LiteralString
) -> AsyncGenerator[AsyncCopy]:
    """Keep row writes cancellable and shield COPY/transaction/pool teardown."""
    stack = AsyncExitStack()
    try:
        connection = await stack.enter_async_context(pool.connection())
        _ = await stack.enter_async_context(connection.transaction())
        cursor = await stack.enter_async_context(connection.cursor())
        copy = await stack.enter_async_context(cursor.copy(query))
    except BaseException as exc:
        with anyio.CancelScope(shield=True):
            _ = await stack.__aexit__(type(exc), exc, exc.__traceback__)
        raise

    try:
        yield copy
        await anyio.lowlevel.checkpoint()
    except BaseException as exc:
        with anyio.CancelScope(shield=True):
            _ = await stack.__aexit__(type(exc), exc, exc.__traceback__)
        raise
    else:
        with anyio.CancelScope(shield=True):
            _ = await stack.__aexit__(None, None, None)


async def _publish_snapshot(
    pool: AsyncConnectionPool,
    snapshot_id: str,
    row_type: Literal["ib", "rha"],
    publish_query: LiteralString,
) -> None:
    async with _pool_transaction(pool) as connection:
        cursor = await connection.execute(
            """SELECT status FROM ubwfris_snapshots
               WHERE id = %s AND row_type = %s FOR UPDATE""",
            (snapshot_id, row_type),
        )
        if await cursor.fetchone() != ("running",):
            raise RuntimeError("UBWFRIS snapshot is not running")
        _ = await connection.execute(publish_query, (snapshot_id,))
        if row_type == "ib":
            _ = await connection.execute(
                "DELETE FROM ubwfris_ib_rows_staging WHERE snapshot_id = %s",
                (snapshot_id,),
            )
        else:
            _ = await connection.execute(
                "DELETE FROM ubwfris_rha_rows_staging WHERE snapshot_id = %s",
                (snapshot_id,),
            )
        cursor = await connection.execute(
            """UPDATE ubwfris_snapshots SET status = 'complete'
               WHERE id = %s AND row_type = %s AND status = 'running'
               RETURNING id""",
            (snapshot_id, row_type),
        )
        if await cursor.fetchone() is None:
            raise RuntimeError("UBWFRIS snapshot is not running")


async def _discard_snapshot(
    pool: AsyncConnectionPool,
    snapshot_id: str,
    row_type: Literal["ib", "rha"],
    rows_table: Literal["ubwfris_ib_rows_staging", "ubwfris_rha_rows_staging"],
) -> None:
    with anyio.CancelScope(shield=True):
        async with pool.connection() as connection, connection.transaction():
            cursor = await connection.execute(
                """SELECT status FROM ubwfris_snapshots
                   WHERE id = %s AND row_type = %s FOR UPDATE""",
                (snapshot_id, row_type),
            )
            snapshot = await cursor.fetchone()
            if snapshot != ("running",):
                return

            if rows_table == "ubwfris_ib_rows_staging":
                _ = await connection.execute(
                    "DELETE FROM ubwfris_ib_rows_staging WHERE snapshot_id = %s",
                    (snapshot_id,),
                )
            else:
                _ = await connection.execute(
                    "DELETE FROM ubwfris_rha_rows_staging WHERE snapshot_id = %s",
                    (snapshot_id,),
                )
            _ = await connection.execute(
                """DELETE FROM ubwfris_snapshots
                   WHERE id = %s AND row_type = %s AND status = 'running'""",
                (snapshot_id, row_type),
            )


async def interrupt_ubwfris_snapshots(pool: AsyncConnectionPool) -> None:
    """Delete snapshots left incomplete by a process crash."""
    with anyio.CancelScope(shield=True):
        async with pool.connection() as connection, connection.transaction():
            _ = await connection.execute(
                """DELETE FROM ubwfris_ib_rows_staging AS rows
                   USING ubwfris_snapshots AS snapshot
                   WHERE rows.snapshot_id = snapshot.id
                     AND snapshot.row_type = 'ib'
                     AND snapshot.status = 'running'"""
            )
            _ = await connection.execute(
                """DELETE FROM ubwfris_rha_rows_staging AS rows
                   USING ubwfris_snapshots AS snapshot
                   WHERE rows.snapshot_id = snapshot.id
                     AND snapshot.row_type = 'rha'
                     AND snapshot.status = 'running'"""
            )
            _ = await connection.execute(
                "DELETE FROM ubwfris_snapshots WHERE status = 'running'"
            )


class PostgresIbWriter(UbwfrisWriter[IbRow]):
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool: AsyncConnectionPool = pool

    @override
    async def begin_snapshot(self, snapshot_id: str) -> None:
        try:
            with anyio.CancelScope(shield=True):
                async with self._pool.connection() as connection:
                    _ = await connection.execute(
                        """INSERT INTO ubwfris_snapshots (id, row_type, status)
                           VALUES (%s, 'ib', 'running')""",
                        (snapshot_id,),
                    )
        except UniqueViolation as exc:
            raise DuplicateUpload(
                "The uploaded workbook was previously imported"
            ) from exc

    @override
    async def discard_snapshot(self, snapshot_id: str) -> None:
        await _discard_snapshot(
            self._pool, snapshot_id, "ib", "ubwfris_ib_rows_staging"
        )

    @override
    async def publish_snapshot(self, snapshot_id: str) -> None:
        await _publish_snapshot(self._pool, snapshot_id, "ib", _PUBLISH_IB_SNAPSHOT)

    @override
    async def stage_ubwfris_rows(self, snapshot_id: str, rows: Sequence[IbRow]) -> int:
        if not rows:
            return 0
        try:
            async with _copy_transaction(
                self._pool,
                """COPY ubwfris_ib_rows_staging (
                        snapshot_id,
                        row_number,
                        project_id,
                        project_name,
                        employee_name,
                        budgeted_hours,
                        projected_hours,
                        booked_hours,
                        period,
                        remaining_hours,
                        cost_center
                    ) FROM STDIN""",
            ) as copy:
                for row in rows:
                    await copy.write_row(
                        (
                            snapshot_id,
                            row.row_number,
                            row.project_id,
                            row.project_name,
                            row.employee_name,
                            row.budgeted_hours,
                            row.projected_hours,
                            row.booked_hours,
                            row.period,
                            row.remaining_hours,
                            row.cost_center,
                        )
                    )
            return len(rows)
        except UniqueViolation as exc:
            raise DuplicateUpload(
                "The uploaded workbook was previously imported"
            ) from exc


class PostgresRhaWriter(UbwfrisWriter[RhaRow]):
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool: AsyncConnectionPool = pool

    @override
    async def begin_snapshot(self, snapshot_id: str) -> None:
        try:
            with anyio.CancelScope(shield=True):
                async with self._pool.connection() as connection:
                    _ = await connection.execute(
                        """INSERT INTO ubwfris_snapshots (id, row_type, status)
                           VALUES (%s, 'rha', 'running')""",
                        (snapshot_id,),
                    )
        except UniqueViolation as exc:
            raise DuplicateUpload(
                "The uploaded workbook was previously imported"
            ) from exc

    @override
    async def discard_snapshot(self, snapshot_id: str) -> None:
        await _discard_snapshot(
            self._pool, snapshot_id, "rha", "ubwfris_rha_rows_staging"
        )

    @override
    async def publish_snapshot(self, snapshot_id: str) -> None:
        await _publish_snapshot(self._pool, snapshot_id, "rha", _PUBLISH_RHA_SNAPSHOT)

    @override
    async def stage_ubwfris_rows(self, snapshot_id: str, rows: Sequence[RhaRow]) -> int:
        if not rows:
            return 0
        try:
            async with _copy_transaction(
                self._pool,
                """COPY ubwfris_rha_rows_staging (
                        snapshot_id,
                        row_number,
                        employee_number,
                        employee_name,
                        project_manager,
                        project_id,
                        project_status,
                        project_name,
                        subproject_id,
                        subproject_status,
                        subproject_name,
                        cost_center,
                        registration_date,
                        hours,
                        project_type,
                        parttime_percentage,
                        main_department,
                        academy,
                        main_department_name,
                        employment_cost_center
                    ) FROM STDIN""",
            ) as copy:
                for row in rows:
                    await copy.write_row(
                        (
                            snapshot_id,
                            row.row_number,
                            row.employee_number,
                            row.employee_name,
                            row.project_manager,
                            row.project_id,
                            row.project_status,
                            row.project_name,
                            row.subproject_id,
                            row.subproject_status,
                            row.subproject_name,
                            row.cost_center,
                            row.registration_date,
                            row.hours,
                            row.project_type,
                            row.parttime_percentage,
                            row.main_department,
                            row.academy,
                            row.main_department_name,
                            row.employment_cost_center,
                        )
                    )
            return len(rows)
        except UniqueViolation as exc:
            raise DuplicateUpload(
                "The uploaded workbook was previously imported"
            ) from exc
