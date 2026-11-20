from contextlib import asynccontextmanager

from azure.identity import InteractiveBrowserCredential
from fastapi.applications import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from msgraph.graph_service_client import GraphServiceClient
from psycopg.conninfo import make_conninfo
from psycopg_pool import AsyncConnectionPool

from api.routes.documents.router import router as documents_router
from api.routes.employees.router import router as employees_router
from api.routes.ubwfris.snapshots.router import router as ubwfris_snapshots_router
from config import config
from infrastructure.msgraph.document_metadata_source import (
    SharePointDocumentMetadataSource,
)
from infrastructure.msgraph.employee_source import (
    MsGraphEmployeeSource,
)
from infrastructure.msgraph.group_drive_source import MsGraphGroupDriveSource
from infrastructure.postgres.document_store import PostgresDocumentStore
from infrastructure.postgres.employee_writer import EmployeePostgresWriter
from infrastructure.postgres.ubwfris_writer import interrupt_ubwfris_snapshots


@asynccontextmanager
async def lifespan(app: FastAPI):
    conninfo = make_conninfo(
        dbname=config.postgres_db,
        user=config.postgres_user,
        password=config.postgres_password,
        host=config.postgres_host,
        port=config.postgres_port,
    )
    credential = InteractiveBrowserCredential(
        tenant_id=config.graph_tenant_id,
        client_id=config.graph_client_id,
        redirect_uri=config.graph_redirect_uri,
    )
    try:
        client = GraphServiceClient(
            credentials=credential,
            scopes=[],
        )

        app.state.employee_source = MsGraphEmployeeSource(client, config.group_id)
        app.state.document_metadata_source = SharePointDocumentMetadataSource(client)
        app.state.group_drive_source = MsGraphGroupDriveSource(client)
        app.state.graph_client = client

        pool: AsyncConnectionPool = AsyncConnectionPool(conninfo=conninfo, open=False)
        async with pool:
            app.state.postgres_pool = pool
            # Crash recovery clears only staging from unfinished runs.
            # The single-instance deployment makes prior running rows orphaned.
            await PostgresDocumentStore(pool).interrupt_metadata_runs()
            await EmployeePostgresWriter(pool).interrupt_employee_sync_runs()
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
app.include_router(documents_router)
app.include_router(ubwfris_snapshots_router)
