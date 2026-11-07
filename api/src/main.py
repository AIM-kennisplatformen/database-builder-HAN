"""FastAPI application setup for the HAN management API."""

from asyncio import to_thread
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from azure.identity import InteractiveBrowserCredential
from fastapi import FastAPI
from msgraph.graph_service_client import GraphServiceClient

from api.routes.teams import handle_team_service_error
from api.routes.teams import router as teams_router
from api.routes.users import router as users_router
from core.config import config
from db.postgres.user_repository import PostgresUserRepository
from services.team_service import TeamService, TeamServiceError
from services.user_service import MsGraphApiUserService

ALEMBIC_CONFIG = Path(__file__).resolve().parents[1] / "pyproject.toml"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await to_thread(command.upgrade, Config(toml_file=str(ALEMBIC_CONFIG)), "head")
    repository = PostgresUserRepository(config)
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
        app.state.user_repository = repository
        app.state.user_service = MsGraphApiUserService(
            client, config.group_id, repository
        )
        app.state.team_service = TeamService(client)
        app.state.config = config
        yield
    finally:
        credential.close()


app = FastAPI(title="HAN management API", lifespan=lifespan)
app.add_exception_handler(TeamServiceError, handle_team_service_error)
app.include_router(users_router)
app.include_router(teams_router)
