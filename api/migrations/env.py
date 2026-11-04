"""Alembic connection setup for the HAN PostgreSQL database."""

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

from core.config import config


def run_migrations_online() -> None:
    url = URL.create(
        "postgresql+psycopg",
        username=config.postgres_user,
        password=config.postgres_password,
        host=config.postgres_host,
        port=config.postgres_port,
        database=config.postgres_db,
    )
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=None)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Run migrations with a PostgreSQL connection")
run_migrations_online()
