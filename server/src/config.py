"""Load and validate application settings before serving requests."""

from pathlib import Path
from typing import Annotated, ClassVar
from urllib.parse import urlparse

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
MSAL_RESERVED_SCOPES = frozenset({"openid", "profile", "offline_access"})

_ = load_dotenv(ENV_FILE)


class _EmployeeGroupConfig(BaseModel):
    id: str = ""


class _GraphConfig(BaseModel):
    client_id: str = ""
    tenant_id: str = ""
    scopes: Annotated[tuple[str, ...], NoDecode] = ()
    employee_group: _EmployeeGroupConfig = Field(default_factory=_EmployeeGroupConfig)
    redirect_uri: str = Field(default="", min_length=1, validate_default=True)
    browser_auto_sign_in: bool = False

    @field_validator("scopes", mode="before")
    @classmethod
    def parse_graph_scopes(cls, value: str | tuple[str, ...]) -> tuple[str, ...]:
        scopes = value.split() if isinstance(value, str) else value
        result = tuple(
            scope for scope in scopes if scope.lower() not in MSAL_RESERVED_SCOPES
        )
        return result

    @field_validator("redirect_uri")
    @classmethod
    def validate_redirect_uri(cls, value: str) -> str:
        redirect = urlparse(value)
        if redirect.scheme not in {"http", "https"} or not redirect.netloc:
            raise ValueError("GRAPH__REDIRECT_URI must be an HTTP URL")
        return value


class _PostgresConfig(BaseModel):
    db: str = ""
    user: str = ""
    password: str = ""
    host: str = "127.0.0.1"
    port: int = 5432


class _DeleteEndpointConfig(BaseModel):
    employee: bool = False
    metadata: bool = False
    ubwfris: bool = False


class Config(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=ENV_FILE,
        env_nested_delimiter="__",
        extra="ignore",
    )

    stream_progress_interval_seconds: float = Field(default=0.25, ge=0)
    postgres: _PostgresConfig = Field(default_factory=_PostgresConfig)
    graph: _GraphConfig = Field(default_factory=_GraphConfig)
    delete_endpoints: _DeleteEndpointConfig = Field(
        default_factory=_DeleteEndpointConfig
    )


config = Config()
