"""Base for Postgres store adapters that share one connection pool."""

from psycopg_pool import AsyncConnectionPool


class PostgresPoolStore:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool: AsyncConnectionPool = pool
