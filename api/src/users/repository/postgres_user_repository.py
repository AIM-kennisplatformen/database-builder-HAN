"""PostgreSQL storage for current users and raw Graph source versions."""

from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import psycopg
from psycopg.types.json import Jsonb

from config import AppConfig
from database import connect
from users.service.ms_graph_api_user_service import (
    GraphUserSnapshot,
    user_from_snapshot,
)
from users.user import User


class PostgresUserRepository:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    async def _connect(self) -> psycopg.AsyncConnection:
        return await connect(self.config)

    async def users(self, limit: int | None = None) -> AsyncIterator[User]:
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")
        async with await self._connect() as connection:
            cursor = await connection.execute(
                """SELECT id, display_name, given_name, surname, mail,
                          company_name, department, job_title, skills,
                          responsibilities, interests
                   FROM user_ms_graph_current ORDER BY id LIMIT %s""",
                (limit,),
            )
            for row in await cursor.fetchall():
                yield User.model_validate(row)

    async def save_graph_snapshot(self, snapshot: GraphUserSnapshot) -> None:
        user = user_from_snapshot(snapshot)
        async with await self._connect() as connection:
            # Serialize refreshes for one Graph ID before inspecting its open range.
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (user.id,)
            )
            cursor = await connection.execute(
                """SELECT lower(valid_at) AS valid_from
                   FROM user_ms_graph_sources
                   WHERE id = %s AND upper_inf(valid_at)
                   FOR UPDATE""",
                (user.id,),
            )
            current = await cursor.fetchone()
            cursor = await connection.execute("SELECT clock_timestamp() AS recorded_at")
            recorded_at: datetime = (await cursor.fetchone())["recorded_at"]
            if current:
                valid_from = current["valid_from"]
                if recorded_at <= valid_from:
                    recorded_at = valid_from + timedelta(microseconds=1)
                await connection.execute(
                    """UPDATE user_ms_graph_sources
                       SET valid_at = tstzrange(lower(valid_at), %s, '[)'),
                           updated_at = clock_timestamp()
                       WHERE id = %s AND upper_inf(valid_at)""",
                    (recorded_at, user.id),
                )

            await connection.execute(
                """INSERT INTO user_ms_graph_sources (
                       id, valid_at, is_present, display_name, given_name,
                       surname, mail, company_name, department, job_title,
                       skills, responsibilities, interests, response_body,
                       request_url
                   ) VALUES (
                       %s, tstzrange(%s, NULL, '[)'), true, %s, %s,
                       %s, %s, %s, %s, %s,
                       %s, %s, %s, %s,
                       %s
                   )""",
                (
                    user.id,
                    recorded_at,
                    user.display_name,
                    user.given_name,
                    user.surname,
                    user.mail,
                    user.company_name,
                    user.department,
                    user.job_title,
                    user.skills,
                    user.responsibilities,
                    user.interests,
                    Jsonb(snapshot["response_body"]),
                    snapshot["request_url"],
                ),
            )
