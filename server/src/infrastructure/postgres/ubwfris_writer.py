"""Persist IB and RHA source rows in separate UBW FRIS snapshot tables."""

from collections.abc import Sequence
from typing import override

import anyio
from psycopg.errors import UniqueViolation
from psycopg_pool import AsyncConnectionPool

from domain.exceptions.ubwfris_ingest_errors import DuplicateUpload
from domain.models.ubwfris import IbRow, RhaRow
from domain.ports.ubwfris.ubwfris_store import UbwfrisWriter


class PostgresIbWriter(UbwfrisWriter[IbRow]):
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool: AsyncConnectionPool = pool

    @override
    async def begin_snapshot(self, snapshot_id: str) -> None:
        try:
            with anyio.CancelScope(shield=True):
                async with self._pool.connection() as connection:
                    _ = await connection.execute(
                        "INSERT INTO ubwfris_snapshots (id, row_type) VALUES (%s, 'ib')",
                        (snapshot_id,),
                    )
        except UniqueViolation as exc:
            raise DuplicateUpload(
                "The uploaded workbook was previously imported"
            ) from exc

    @override
    async def discard_snapshot(self, snapshot_id: str) -> None:
        with anyio.CancelScope(shield=True):
            async with self._pool.connection() as connection, connection.transaction():
                _ = await connection.execute(
                    "DELETE FROM ubwfris_ib_rows WHERE snapshot_id = %s", (snapshot_id,)
                )
                _ = await connection.execute(
                    "DELETE FROM ubwfris_snapshots WHERE id = %s AND row_type = 'ib'",
                    (snapshot_id,),
                )

    @override
    async def write_ubwfris_rows(self, snapshot_id: str, rows: Sequence[IbRow]) -> int:
        if not rows:
            return 0
        try:
            async with (
                self._pool.connection() as connection,
                connection.transaction(),
                connection.cursor() as cursor,
                cursor.copy(
                    """COPY ubwfris_ib_rows (
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
                    ) FROM STDIN"""
                ) as copy,
            ):
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
                        "INSERT INTO ubwfris_snapshots (id, row_type) VALUES (%s, 'rha')",
                        (snapshot_id,),
                    )
        except UniqueViolation as exc:
            raise DuplicateUpload(
                "The uploaded workbook was previously imported"
            ) from exc

    @override
    async def discard_snapshot(self, snapshot_id: str) -> None:
        with anyio.CancelScope(shield=True):
            async with self._pool.connection() as connection, connection.transaction():
                _ = await connection.execute(
                    "DELETE FROM ubwfris_rha_rows WHERE snapshot_id = %s",
                    (snapshot_id,),
                )
                _ = await connection.execute(
                    "DELETE FROM ubwfris_snapshots WHERE id = %s AND row_type = 'rha'",
                    (snapshot_id,),
                )

    @override
    async def write_ubwfris_rows(self, snapshot_id: str, rows: Sequence[RhaRow]) -> int:
        if not rows:
            return 0
        try:
            async with (
                self._pool.connection() as connection,
                connection.transaction(),
                connection.cursor() as cursor,
                cursor.copy(
                    """COPY ubwfris_rha_rows (
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
                    ) FROM STDIN"""
                ) as copy,
            ):
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
