from contextlib import asynccontextmanager

from azure.identity import InteractiveBrowserCredential
from fastapi.applications import FastAPI
from msgraph.graph_service_client import GraphServiceClient

from api.employees.employee_sync_route import router as employees_router
from config import config
from infrastructure.persistence.postgres_employee_store import PostgresEmployeeStore
from infrastructure.sources.ms_graph_employee_source import (
    MsGraphEmployeeSource,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
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
        app.state.employee_store = PostgresEmployeeStore()
        yield
    finally:
        credential.close()


app = FastAPI(title="HAN API", version="1.0.0", lifespan=lifespan)
app.include_router(employees_router)
