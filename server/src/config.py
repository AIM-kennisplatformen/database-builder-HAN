"""Load and validate application settings before serving requests."""

from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
MSAL_RESERVED_SCOPES = frozenset({"openid", "profile", "offline_access"})

_ = load_dotenv(ENV_FILE)


class Config(BaseSettings):
    postgres_db: str = ""
    postgres_user: str = ""
    postgres_password: str = ""
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432

    graph_client_id: str = ""
    graph_tenant_id: str = ""
    graph_scopes: Annotated[tuple[str, ...], NoDecode] = ()
    group_id: str = ""
    graph_redirect_uri: str = Field(default="", min_length=1, validate_default=True)
    graph_browser_auto_sign_in: bool = False

    @field_validator("graph_scopes", mode="before")
    @classmethod
    def parse_graph_scopes(cls, value: str | tuple[str, ...]) -> tuple[str, ...]:
        scopes = value.split() if isinstance(value, str) else value
        result = tuple(
            scope for scope in scopes if scope.lower() not in MSAL_RESERVED_SCOPES
        )
        # if not result:
        #     raise ValueError(
        #         "GRAPH_SCOPES must include a Microsoft Graph permission scope"
        #     )
        return result

    @field_validator("graph_redirect_uri")
    @classmethod
    def validate_redirect_uri(cls, value: str) -> str:
        redirect = urlparse(value)
        if redirect.scheme not in {"http", "https"} or not redirect.netloc:
            raise ValueError("GRAPH_REDIRECT_URI must be an HTTP URL")
        return value


config = Config()
