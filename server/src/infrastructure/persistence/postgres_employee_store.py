"""Save employees in PostgreSQL while retaining changed source versions."""

from datetime import datetime
from typing import cast, override

from psycopg import AsyncConnection

from config import config
from domain.models.employee import Employee
from domain.ports.persistence.employee_store import EmployeeStore


def _values(employee: Employee) -> tuple[object, ...]:
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


class PostgresEmployeeStore(EmployeeStore):
    @override
    async def save_employee(self, employee: Employee) -> None:
        values = _values(employee)
        async with (
            await AsyncConnection.connect(
                dbname=config.postgres_db,
                user=config.postgres_user,
                password=config.postgres_password,
                host=config.postgres_host,
                port=config.postgres_port,
            ) as connection,
            connection.transaction(),
            connection.cursor() as cursor,
        ):
            # Serialize writes so two requests cannot create overlapping versions.
            _ = await cursor.execute(
                "SELECT pg_advisory_xact_lock(7368369943385156101)"
            )
            _ = await cursor.execute(
                """
                SELECT is_present, display_name, given_name, surname, mail,
                       company_name, department, job_title, skills,
                       responsibilities, interests
                FROM user_ms_graph_sources
                WHERE id = %s AND upper_inf(valid_at)
                FOR UPDATE
                """,
                (employee.id,),
            )
            current = await cursor.fetchone()
            if current == (True, *values):
                return

            _ = await cursor.execute(
                """
                SELECT GREATEST(
                    clock_timestamp(),
                    COALESCE(
                        max(lower(valid_at)) + interval '1 microsecond',
                        '-infinity'::timestamptz
                    )
                )
                FROM user_ms_graph_sources
                WHERE id = %s AND upper_inf(valid_at)
                """,
                (employee.id,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise RuntimeError("PostgreSQL did not return a sync timestamp")
            at = cast(datetime, row[0])

            if current is not None:
                _ = await cursor.execute(
                    """
                    UPDATE user_ms_graph_sources
                    SET valid_at = tstzrange(lower(valid_at), %s, '[)'),
                        updated_at = %s
                    WHERE id = %s AND upper_inf(valid_at)
                    """,
                    (at, at, employee.id),
                )
            _ = await cursor.execute(
                """
                INSERT INTO user_ms_graph_sources (
                    id, valid_at, is_present, display_name, given_name,
                    surname, mail, company_name, department, job_title,
                    skills, responsibilities, interests
                ) VALUES (
                    %s, tstzrange(%s, NULL, '[)'), true, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s
                )
                """,
                (employee.id, at, *values),
            )
