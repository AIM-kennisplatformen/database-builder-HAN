"""FastAPI application setup for the HAN management API."""

from asyncio import to_thread
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from msgraph.graph_service_client import GraphServiceClient

from config import load_config
from users.controller.user_controller import router as ms_graph_users_router
from users.repository.postgres_user_repository import PostgresUserRepository
from users.service.ms_graph_api_user_service import (
    InteractiveBrowserAuth,
    MsGraphApiUserService,
)

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    await to_thread(command.upgrade, Config(str(ALEMBIC_INI)), "head")
    auth = InteractiveBrowserAuth(
        config.graph_tenant_id, config.graph_client_id, config.graph_redirect_uri
    )
    repository = PostgresUserRepository(config)
    credential = auth.create_credential()
    try:
        client = GraphServiceClient(
            credentials=credential,
            scopes=list(config.graph_scopes),
        )
        app.state.auth_strategy = auth
        app.state.user_repository = repository
        app.state.user_service = MsGraphApiUserService(
            client, config.group_id, repository
        )
        app.state.config = config
        yield
    finally:
        credential.close()


app = FastAPI(title="HAN management API", lifespan=lifespan)
app.include_router(ms_graph_users_router)
