from contextlib import asynccontextmanager

from azure.identity import InteractiveBrowserCredential
from fastapi.applications import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from msgraph.graph_service_client import GraphServiceClient
from psycopg.conninfo import make_conninfo
from psycopg_pool import AsyncConnectionPool

from api.routes.documents.router import router as documents_router
from api.routes.employees.router import router as employees_router
from api.routes.operations import router as operations_router
from api.routes.ubwfris.snapshots.router import router as ubwfris_snapshots_router
from config import config
from infrastructure.msgraph.document_metadata.source import (
    DocumentMetadataSourceSharePoint,
)
from infrastructure.msgraph.employee_source import (
    EmployeeSourceMsGraph,
)
from infrastructure.msgraph.group_drive_source import GroupDriveSourceMsGraph
from infrastructure.postgres.document_store import DocumentStorePostgres
from infrastructure.postgres.employee_writer import EmployeeWriterPostgres
from infrastructure.postgres.ubwfris_writer import interrupt_ubwfris_snapshots


@asynccontextmanager
async def lifespan(app: FastAPI):
    conninfo = make_conninfo(
        dbname=config.postgres.db,
        user=config.postgres.user,
        password=config.postgres.password,
        host=config.postgres.host,
        port=config.postgres.port,
    )
    credential = InteractiveBrowserCredential(
        tenant_id=config.graph.tenant_id,
        client_id=config.graph.client_id,
        redirect_uri=config.graph.redirect_uri,
    )
    try:
        client = GraphServiceClient(
            credentials=credential,
            scopes=[],
        )

        app.state.employee_source = EmployeeSourceMsGraph(
            client, config.graph.employee_group.id
        )
        app.state.document_metadata_source = DocumentMetadataSourceSharePoint(client)
        app.state.group_drive_source = GroupDriveSourceMsGraph(client)
        app.state.graph_client = client

        pool: AsyncConnectionPool = AsyncConnectionPool(conninfo=conninfo, open=False)
        async with pool:
            app.state.postgres_pool = pool
            # Crash recovery clears only staging from unfinished runs.
            # The single-instance deployment makes prior running rows orphaned.
            await DocumentStorePostgres(pool).interrupt_metadata_runs()
            await EmployeeWriterPostgres(pool).interrupt_employee_sync_runs()
            await interrupt_ubwfris_snapshots(pool)
            yield
    finally:
        credential.close()


app = FastAPI(title="HAN API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(employees_router)
app.include_router(operations_router)
app.include_router(documents_router)
app.include_router(ubwfris_snapshots_router)
