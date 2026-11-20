from typing import Annotated, Protocol

from fastapi import Depends, HTTPException
from fastapi.requests import Request
from msgraph.graph_service_client import GraphServiceClient
from psycopg_pool import AsyncConnectionPool

from domain.models.ubwfris import IbRow, RhaRow
from domain.ports.documents.document_metadata_source import DocumentRefreshSource
from domain.ports.documents.document_store import DocumentRefreshStore
from domain.ports.documents.group_drive_source import GroupDriveSource
from domain.ports.employees.employee_source import EmployeeSource
from domain.ports.employees.employee_store import EmployeeWriter
from domain.ports.operation_run_store import OperationRunReader
from domain.ports.ubwfris.ubwfris_parser import UbwfrisParser
from domain.ports.ubwfris.ubwfris_store import UbwfrisWriter
from infrastructure.excel.ubwfris_parser import (
    UbwfrisIbExcelParser,
    UbwfrisRhaExcelParser,
)
from infrastructure.postgres.document_store import PostgresDocumentStore
from infrastructure.postgres.employee_writer import EmployeePostgresWriter
from infrastructure.postgres.operation_run_store import PostgresOperationRunStore
from infrastructure.postgres.ubwfris_writer import (
    PostgresIbWriter,
    PostgresRhaWriter,
)


class ApplicationState(Protocol):
    employee_source: EmployeeSource
    document_metadata_source: DocumentRefreshSource
    group_drive_source: GroupDriveSource
    graph_client: GraphServiceClient
    postgres_pool: AsyncConnectionPool


def _state(request: Request) -> ApplicationState:
    return request.app.state  # pyright: ignore[reportAny]


def get_employee_source(request: Request) -> EmployeeSource:
    return _state(request).employee_source


def get_postgres_pool(request: Request) -> AsyncConnectionPool:
    return _state(request).postgres_pool


def get_operation_run_reader(
    pool: Annotated[AsyncConnectionPool, Depends(get_postgres_pool)],
) -> OperationRunReader:
    return PostgresOperationRunStore(pool)


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


def get_document_metadata_source(request: Request) -> DocumentRefreshSource:
    return _state(request).document_metadata_source


def get_document_store(
    pool: Annotated[AsyncConnectionPool, Depends(get_postgres_pool)],
) -> DocumentRefreshStore:
    return PostgresDocumentStore(pool)


def get_group_drive_source(request: Request) -> GroupDriveSource:
    return _state(request).group_drive_source


def get_graph_client(request: Request) -> GraphServiceClient:
    return _state(request).graph_client


async def get_current_graph_user_id(
    client: Annotated[GraphServiceClient, Depends(get_graph_client)],
) -> str:
    try:
        user = await client.me.get()
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Unable to identify the current Graph user"
        ) from exc
    if user is None or not user.id:
        raise HTTPException(status_code=503, detail="Graph returned no current user ID")
    return user.id
