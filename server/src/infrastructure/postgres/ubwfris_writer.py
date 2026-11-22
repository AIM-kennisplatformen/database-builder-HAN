"""Persist IB and RHA source rows in separate UBW FRIS snapshot tables."""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from json import dumps
from typing import Literal, LiteralString, override
from uuid import UUID

import anyio
from psycopg import AsyncConnection, AsyncCopy
from psycopg.errors import UniqueViolation
from psycopg_pool import AsyncConnectionPool

from domain.exceptions.ubwfris_ingest_errors import DuplicateUpload, InvalidWorkbook
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
_UBWFRIS_RESET_LOCKS: dict[Literal["ib", "rha"], int] = {
    "ib": 7368369943385156104,
    "rha": 7368369943385156105,
}
_DELETE_SNAPSHOT_ROWS: dict[Literal["ib", "rha"], LiteralString] = {
    "ib": "DELETE FROM ubwfris_ib_rows",
    "rha": "DELETE FROM ubwfris_rha_rows",
}
_DELETE_STAGING_ROWS: dict[Literal["ib", "rha"], LiteralString] = {
    "ib": "DELETE FROM ubwfris_ib_rows_staging WHERE snapshot_id = %s",
    "rha": "DELETE FROM ubwfris_rha_rows_staging WHERE snapshot_id = %s",
}
_DELETE_ALL_STAGING_ROWS: dict[Literal["ib", "rha"], LiteralString] = {
    "ib": "DELETE FROM ubwfris_ib_rows_staging",
    "rha": "DELETE FROM ubwfris_rha_rows_staging",
}


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
            _ = await connection.execute(
                """UPDATE operation_runs SET status = 'interrupted', finished_at = now(),
                   failure_code = 'process_interrupted'
                   WHERE kind IN ('ubwfris.ib', 'ubwfris.rha') AND status = 'running'"""
            )


class _UbwfrisWriterPostgres[T: IbRow | RhaRow](ABC):
    @property
    @abstractmethod
    def _row_type(self) -> Literal["ib", "rha"]: ...

    @property
    @abstractmethod
    def _publish_query(self) -> LiteralString: ...

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool: AsyncConnectionPool = pool

    async def delete_all_snapshots(self, row_type: Literal["ib", "rha"]) -> bool:
        operation_kind = f"ubwfris.{row_type}"
        async with _pool_transaction(self._pool) as connection:
            _ = await connection.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                (_UBWFRIS_RESET_LOCKS[row_type],),
            )
            cursor = await connection.execute(
                """SELECT id FROM operation_runs
                   WHERE kind = %s AND status = 'running' FOR UPDATE""",
                (operation_kind,),
            )
            if await cursor.fetchone() is not None:
                return False
            cursor = await connection.execute(
                """SELECT id FROM ubwfris_snapshots
                   WHERE row_type = %s AND status = 'running' FOR UPDATE""",
                (row_type,),
            )
            if await cursor.fetchone() is not None:
                return False
            _ = await connection.execute(_DELETE_SNAPSHOT_ROWS[row_type])
            _ = await connection.execute(_DELETE_ALL_STAGING_ROWS[row_type])
            _ = await connection.execute(
                "DELETE FROM ubwfris_snapshots WHERE row_type = %s", (row_type,)
            )
            _ = await connection.execute(
                "DELETE FROM operation_runs WHERE kind = %s", (operation_kind,)
            )
            return True

    async def begin_operation(
        self, operation_id: UUID, principal_id: str, snapshot_id: str
    ) -> None:
        async with self._pool.connection() as connection:
            _ = await connection.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                (_UBWFRIS_RESET_LOCKS[self._row_type],),
            )
            _ = await connection.execute(
                """INSERT INTO operation_runs
                   (id, principal_id, kind, status, domain_ids)
                   VALUES (%s, %s, %s, 'running', %s::jsonb)""",
                (
                    operation_id,
                    principal_id,
                    f"ubwfris.{self._row_type}",
                    dumps({"snapshot_id": snapshot_id, "row_type": self._row_type}),
                ),
            )

    async def fail_operation(
        self,
        operation_id: UUID,
        failure_code: str,
        summary: dict[str, object],
        *,
        interrupted: bool = False,
    ) -> None:
        async with self._pool.connection() as connection:
            _ = await connection.execute(
                """UPDATE operation_runs SET status = %s, finished_at = now(),
                   failure_code = %s, summary = %s::jsonb
                   WHERE id = %s AND status = 'running'""",
                (
                    "interrupted" if interrupted else "failed",
                    failure_code,
                    dumps(summary),
                    operation_id,
                ),
            )

    async def begin_snapshot(self, snapshot_id: str) -> None:
        try:
            with anyio.CancelScope(shield=True):
                async with self._pool.connection() as connection:
                    _ = await connection.execute(
                        """INSERT INTO ubwfris_snapshots (id, row_type, status)
                           VALUES (%s, %s, 'running')""",
                        (snapshot_id, self._row_type),
                    )
        except UniqueViolation as exc:
            raise DuplicateUpload(
                "The uploaded workbook was previously imported"
            ) from exc

    async def discard_snapshot(self, snapshot_id: str) -> None:
        with anyio.CancelScope(shield=True):
            async with self._pool.connection() as connection, connection.transaction():
                cursor = await connection.execute(
                    """SELECT status FROM ubwfris_snapshots
                       WHERE id = %s AND row_type = %s FOR UPDATE""",
                    (snapshot_id, self._row_type),
                )
                if await cursor.fetchone() != ("running",):
                    return
                _ = await connection.execute(
                    _DELETE_STAGING_ROWS[self._row_type],
                    (snapshot_id,),
                )
                _ = await connection.execute(
                    """DELETE FROM ubwfris_snapshots
                       WHERE id = %s AND row_type = %s AND status = 'running'""",
                    (snapshot_id, self._row_type),
                )

    async def publish_snapshot(
        self, snapshot_id: str, operation_id: UUID, summary: dict[str, object]
    ) -> None:
        async with _pool_transaction(self._pool) as connection:
            cursor = await connection.execute(
                """SELECT status FROM ubwfris_snapshots
                   WHERE id = %s AND row_type = %s FOR UPDATE""",
                (snapshot_id, self._row_type),
            )
            if await cursor.fetchone() != ("running",):
                raise RuntimeError("UBWFRIS snapshot is not running")
            _ = await connection.execute(self._publish_query, (snapshot_id,))
            _ = await connection.execute(
                _DELETE_STAGING_ROWS[self._row_type],
                (snapshot_id,),
            )
            cursor = await connection.execute(
                """UPDATE ubwfris_snapshots SET status = 'complete'
                   WHERE id = %s AND row_type = %s AND status = 'running'
                   RETURNING id""",
                (snapshot_id, self._row_type),
            )
            if await cursor.fetchone() is None:
                raise RuntimeError("UBWFRIS snapshot is not running")
            cursor = await connection.execute(
                """UPDATE operation_runs SET status = 'complete', finished_at = now(),
                   summary = %s::jsonb WHERE id = %s AND status = 'running'
                   RETURNING id""",
                (dumps(summary), operation_id),
            )
            if await cursor.fetchone() is None:
                raise RuntimeError("UBWFRIS operation is not running")

    @abstractmethod
    async def stage_ubwfris_rows(self, snapshot_id: str, rows: Sequence[T]) -> int:
        """Stage workbook rows in the writer's row-specific staging table."""
        ...


class IbWriterPostgres(_UbwfrisWriterPostgres[IbRow], UbwfrisWriter[IbRow]):
    @property
    @override
    def _row_type(self) -> Literal["ib"]:
        return "ib"

    @property
    @override
    def _publish_query(self) -> LiteralString:
        return _PUBLISH_IB_SNAPSHOT

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
            raise InvalidWorkbook("Workbook contains duplicate row numbers") from exc


class RhaWriterPostgres(_UbwfrisWriterPostgres[RhaRow], UbwfrisWriter[RhaRow]):
    @property
    @override
    def _row_type(self) -> Literal["rha"]:
        return "rha"

    @property
    @override
    def _publish_query(self) -> LiteralString:
        return _PUBLISH_RHA_SNAPSHOT

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
            raise InvalidWorkbook("Workbook contains duplicate row numbers") from exc
