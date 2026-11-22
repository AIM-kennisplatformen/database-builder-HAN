from datetime import datetime
from typing import cast, override
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from domain.ports.operation_run_store import OperationRun, OperationRunReader


class OperationRunReaderPostgres(OperationRunReader):
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool: AsyncConnectionPool = pool

    @override
    async def get_operation_run(
        self, operation_id: UUID, principal_id: str
    ) -> OperationRun | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT id, kind, status, started_at, finished_at, summary,
                          failure_code, domain_ids
                   FROM operation_runs WHERE id = %s AND principal_id = %s""",
                (operation_id, principal_id),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        values = cast(tuple[object, ...], tuple(row))
        return OperationRun(
            cast(UUID, values[0]),
            cast(str, values[1]),
            cast(str, values[2]),
            cast(datetime, values[3]),
            cast(datetime | None, values[4]),
            cast(dict[str, object], values[5]),
            cast(str | None, values[6]),
            cast(dict[str, object], values[7]),
        )
