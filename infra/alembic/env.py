"""Alembic environment (async, psycopg 3).

Configuration lives in `[tool.alembic]` of pyproject.toml (no alembic.ini).
The database URL comes from the application settings (DATABASE_URL), unless a
caller passes one explicitly via `Config(attributes={"database_url": ...})`.
"""

import asyncio
import logging

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.db.models import Base

config = context.config
target_metadata = Base.metadata

logging.basicConfig(level=logging.INFO, format="%(levelname)-5.5s [%(name)s] %(message)s")
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def _database_url() -> str:
    url: str | None = config.attributes.get("database_url")
    return url or get_settings().database_url


def run_migrations_offline() -> None:
    """Emit the SQL to stdout instead of executing it (`alembic upgrade head --sql`)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
