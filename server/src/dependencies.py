from typing import Annotated, Protocol

from fastapi import Depends
from fastapi.requests import Request
from psycopg_pool import AsyncConnectionPool

from domain.models.ubwfris import IbRow, RhaRow
from domain.ports.persistence.employee_store import EmployeeWriter
from domain.ports.persistence.ubwfris_store import UbwfrisWriter
from domain.ports.sources.employee_source import EmployeeSource
from domain.ports.sources.ubwfris_parser import UbwfrisParser
from infrastructure.persistence.employee_postgres_writer import EmployeePostgresWriter
from infrastructure.persistence.ubwfris_postgres_writer import (
    PostgresIbWriter,
    PostgresRhaWriter,
)
from infrastructure.sources.ubwfris_excel_parser import (
    UbwfrisIbExcelParser,
    UbwfrisRhaExcelParser,
)


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


def get_ubwfris_ib_parser() -> UbwfrisParser[IbRow]:
    return UbwfrisIbExcelParser()


def get_ubwfris_rha_parser() -> UbwfrisParser[RhaRow]:
    return UbwfrisRhaExcelParser()


def get_ubwfris_ib_writer(
    pool: Annotated[AsyncConnectionPool, Depends(get_postgres_pool)],
) -> UbwfrisWriter[IbRow]:
    return PostgresIbWriter(pool)


def get_ubwfris_rha_writer(
    pool: Annotated[AsyncConnectionPool, Depends(get_postgres_pool)],
) -> UbwfrisWriter[RhaRow]:
    return PostgresRhaWriter(pool)
