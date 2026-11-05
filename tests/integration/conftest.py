"""Integration fixtures: a real PostgreSQL + pgvector database, migrated with Alembic.

Set TEST_DATABASE_URL to enable these tests. The database is WIPED on each run,
so its name must end with "_test".
"""

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, make_url, pool, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tests.integration.helpers import run_alembic


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if Path(item.path).is_relative_to(Path(__file__).parent):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    database = make_url(url).database or ""
    if not database.endswith("_test"):
        pytest.fail(f"Refusing to wipe database {database!r}: its name must end with '_test'.")
    return url


def reset_schema(database_url: str) -> None:
    engine = create_engine(database_url, poolclass=pool.NullPool)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()


@pytest.fixture(scope="session")
def migrated_database(database_url: str) -> Iterator[str]:
    reset_schema(database_url)
    result = run_alembic(database_url, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    yield database_url


@pytest.fixture(scope="session")
async def engine(migrated_database: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_database, poolclass=pool.NullPool)
    yield engine
    await engine.dispose()
