"""PostgreSQL storage for current users and raw Graph source versions."""

from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import psycopg
from pydantic import TypeAdapter

from core.config import Config
from db.postgres.postgres import connect
from domain.models.user import User

USER_ROW = TypeAdapter(User)


class PostgresUserRepository:
    def __init__(self, config: Config) -> None:
        self.config: Config = config

    async def _connect(self) -> psycopg.AsyncConnection[dict[str, object]]:
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
                yield USER_ROW.validate_python(row)

    async def save_ms_graph_user(self, user: User) -> None:
        async with await self._connect() as connection:
            # Serialize refreshes for one Graph ID before inspecting its open range.
            _ = await connection.execute(
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
            recorded_row = await cursor.fetchone()
            if recorded_row is None:
                raise RuntimeError("PostgreSQL did not return a timestamp")
            recorded_at = recorded_row["recorded_at"]
            if not isinstance(recorded_at, datetime):
                raise TypeError("PostgreSQL returned an invalid timestamp")
            if current:
                valid_from = current["valid_from"]
                if not isinstance(valid_from, datetime):
                    raise TypeError("PostgreSQL returned an invalid validity range")
                if recorded_at <= valid_from:
                    recorded_at = valid_from + timedelta(microseconds=1)
                _ = await connection.execute(
                    """UPDATE user_ms_graph_sources
                       SET valid_at = tstzrange(lower(valid_at), %s, '[)'),
                           updated_at = clock_timestamp()
                       WHERE id = %s AND upper_inf(valid_at)""",
                    (recorded_at, user.id),
                )

            _ = await connection.execute(
                """INSERT INTO user_ms_graph_sources (
                       id, valid_at, is_present, display_name, given_name,
                       surname, mail, company_name, department, job_title,
                       skills, responsibilities, interests
                   ) VALUES (
                       %s, tstzrange(%s, NULL, '[)'), true, %s, %s,
                       %s, %s, %s, %s, %s,
                       %s, %s, %s
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
                ),
            )
