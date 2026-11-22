"""Persist combined metadata operation outcomes."""

from json import dumps
from uuid import UUID, uuid4

from psycopg import AsyncConnection

from infrastructure.postgres.document_store_constants import METADATA_RESET_LOCK
from infrastructure.postgres.pool_store import PoolStorePostgres
from infrastructure.postgres.transactions import staging_transaction


class CombinedOperationStoreMethods(PoolStorePostgres):
    async def begin_group_operation(self, principal_id: str, group_id: str) -> UUID:
        operation_id = uuid4()
        async with self._pool.connection() as connection:
            _ = await connection.execute(
                "SELECT pg_advisory_xact_lock(%s)", (METADATA_RESET_LOCK,)
            )
            _ = await connection.execute(
                """INSERT INTO operation_runs
                   (id, principal_id, kind, status, domain_ids)
                   VALUES (%s, %s, 'metadata.ingest', 'running', %s::jsonb)""",
                (
                    operation_id,
                    principal_id,
                    dumps(
                        {
                            "group_id": group_id,
                            "drive_run_ids": [],
                        }
                    ),
                ),
            )
        return operation_id

    async def _set_drive_outcome(
        self,
        connection: AsyncConnection[tuple[object, ...]],
        operation_id: UUID,
        drive_id: str,
        outcome: dict[str, object],
    ) -> None:
        cursor = await connection.execute(
            """UPDATE operation_runs
               SET summary = jsonb_set(
                 jsonb_set(summary, '{drive_outcomes}',
                   COALESCE(summary->'drive_outcomes', '{}'::jsonb), TRUE),
                 ARRAY['drive_outcomes', %s],
                 CASE WHEN summary->'drive_outcomes'->%s->>'status' IN
                   ('published', 'failed', 'skipped', 'interrupted')
                   THEN summary->'drive_outcomes'->%s
                   ELSE %s::jsonb END,
                 TRUE)
               WHERE id = %s AND status = 'running'
               RETURNING id""",
            (drive_id, drive_id, drive_id, dumps(outcome), operation_id),
        )
        if await cursor.fetchone() is None:
            raise ValueError("Combined metadata operation is not active")

    async def record_metadata_drive_outcome(
        self, operation_id: UUID, drive_id: str, outcome: dict[str, object]
    ) -> None:
        async with staging_transaction(self._pool) as connection:
            await self._set_drive_outcome(connection, operation_id, drive_id, outcome)

    async def complete_group_operation(
        self, operation_id: UUID, summary: dict[str, object]
    ) -> None:
        async with staging_transaction(self._pool) as connection:
            cursor = await connection.execute(
                """UPDATE operation_runs SET status = 'complete', finished_at = now(),
                   summary = summary || %s::jsonb
                   WHERE id = %s AND status = 'running'
                   RETURNING id""",
                (dumps(summary), operation_id),
            )
            if await cursor.fetchone() is None:
                raise ValueError("Combined metadata operation is not active")

    async def fail_group_operation(
        self,
        operation_id: UUID,
        failure_code: str,
        *,
        interrupted: bool = False,
        summary: dict[str, object] | None = None,
    ) -> None:
        async with self._pool.connection() as connection:
            _ = await connection.execute(
                """UPDATE operation_runs SET status = %s, finished_at = now(),
                   failure_code = %s,
                   summary = summary || COALESCE(%s::jsonb, '{}'::jsonb)
                   WHERE id = %s AND status = 'running'""",
                (
                    "interrupted" if interrupted else "failed",
                    failure_code,
                    dumps(summary) if summary is not None else None,
                    operation_id,
                ),
            )
