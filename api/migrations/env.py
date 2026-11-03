"""Alembic connection setup for the HAN PostgreSQL database."""

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

from config import load_config


def run_migrations_online() -> None:
    settings = load_config()
    url = URL.create(
        "postgresql+psycopg",
        username=settings.postgres_user,
        password=settings.postgres_password,
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
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
