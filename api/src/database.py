"""Open PostgreSQL connections using validated application settings."""

import psycopg
from psycopg.rows import dict_row

from config import AppConfig


async def connect(config: AppConfig) -> psycopg.AsyncConnection:
    return await psycopg.AsyncConnection.connect(
        host=config.postgres_host,
        port=config.postgres_port,
        dbname=config.postgres_db,
        user=config.postgres_user,
        password=config.postgres_password,
        row_factory=dict_row,
    )
