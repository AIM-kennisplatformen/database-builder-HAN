"""Open PostgreSQL connections using validated application settings."""

import psycopg
from psycopg.rows import dict_row

from core.config import Config


async def connect(config: Config) -> psycopg.AsyncConnection[dict[str, object]]:
    return await psycopg.AsyncConnection[dict[str, object]].connect(
        host=config.postgres_host,
        port=config.postgres_port,
        dbname=config.postgres_db,
        user=config.postgres_user,
        password=config.postgres_password,
        row_factory=dict_row,
    )
