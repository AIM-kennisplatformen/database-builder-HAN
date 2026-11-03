"""Load and validate application settings before serving requests."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
REQUIRED = (
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_PORT",
    "GRAPH_CLIENT_ID",
    "GRAPH_TENANT_ID",
    "GRAPH_SCOPES",
    "GROUP_ID",
    "GRAPH_REDIRECT_URI",
)
MSAL_RESERVED_SCOPES = frozenset({"openid", "profile", "offline_access"})


@dataclass(frozen=True)
class AppConfig:
    postgres_db: str
    postgres_user: str
    postgres_password: str = field(repr=False)
    postgres_port: int
    graph_client_id: str
    graph_tenant_id: str
    graph_scopes: tuple[str, ...]
    group_id: str
    graph_redirect_uri: str
    postgres_host: str = "127.0.0.1"


def load_config(path: Path = ENV_FILE) -> AppConfig:
    if not path.is_file():
        raise ValueError(f"Configuration file is missing: {path}")

    file_values = dotenv_values(path)
    values = {key: os.environ.get(key, file_values.get(key)) for key in REQUIRED}
    missing = [key for key, value in values.items() if not value or not value.strip()]
    if missing:
        raise ValueError(f"Missing configuration values: {', '.join(missing)}")
    validated = {key: value for key, value in values.items() if value is not None}

    try:
        port = int(validated["POSTGRES_PORT"])
    except ValueError:
        raise ValueError("POSTGRES_PORT must be an integer") from None
    if not 1 <= port <= 65535:
        raise ValueError("POSTGRES_PORT must be between 1 and 65535")

    redirect = urlparse(validated["GRAPH_REDIRECT_URI"])
    if redirect.scheme not in {"http", "https"} or not redirect.netloc:
        raise ValueError("GRAPH_REDIRECT_URI must be an HTTP URL")

    scopes = tuple(
        scope
        for scope in validated["GRAPH_SCOPES"].split()
        if scope.lower() not in MSAL_RESERVED_SCOPES and scope is not None
    )
    if not scopes:
        raise ValueError("GRAPH_SCOPES must include a Microsoft Graph permission scope")

    return AppConfig(
        postgres_db=validated["POSTGRES_DB"],
        postgres_user=validated["POSTGRES_USER"],
        postgres_password=validated["POSTGRES_PASSWORD"],
        postgres_port=port,
        graph_client_id=validated["GRAPH_CLIENT_ID"],
        graph_tenant_id=validated["GRAPH_TENANT_ID"],
        graph_scopes=scopes,
        group_id=validated["GROUP_ID"],
        graph_redirect_uri=validated["GRAPH_REDIRECT_URI"],
        postgres_host=os.environ.get("POSTGRES_HOST")
        or file_values.get("POSTGRES_HOST")
        or "127.0.0.1",
    )
