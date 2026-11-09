from contextlib import asynccontextmanager

from azure.identity import InteractiveBrowserCredential
from fastapi.applications import FastAPI
from msgraph.graph_service_client import GraphServiceClient
from psycopg.conninfo import make_conninfo
from psycopg_pool import AsyncConnectionPool

from api.routes.employees.router import router as employees_router
from config import config
from infrastructure.sources.ms_graph_employee_source import (
    MsGraphEmployeeSource,
)


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
            scopes=list(config.graph_scopes),
        )
        app.state.employee_source = MsGraphEmployeeSource(client, config.group_id)
        async with AsyncConnectionPool(conninfo=conninfo, open=False) as pool:
            app.state.postgres_pool = pool
            yield
    finally:
        credential.close()


app = FastAPI(title="HAN API", version="1.0.0", lifespan=lifespan)
app.include_router(employees_router)
