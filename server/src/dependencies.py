from typing import Annotated, Protocol

from fastapi import Depends
from fastapi.requests import Request
from psycopg_pool import AsyncConnectionPool

from domain.ports.persistence.employee_store import EmployeeWriter
from domain.ports.sources.employee_source import EmployeeSource
from infrastructure.persistence.employee_postgres_writer import EmployeePostgresWriter


class ApplicationState(Protocol):
    employee_source: EmployeeSource
    postgres_pool: AsyncConnectionPool


def _state(request: Request) -> ApplicationState:
    return request.app.state  # pyright: ignore[reportAny]


def get_employee_source(request: Request) -> EmployeeSource:
    return _state(request).employee_source


def get_postgres_pool(request: Request) -> AsyncConnectionPool:
    return _state(request).postgres_pool


def get_employee_store(
    pool: Annotated[AsyncConnectionPool, Depends(get_postgres_pool)],
) -> EmployeeWriter:
    return EmployeePostgresWriter(pool)
