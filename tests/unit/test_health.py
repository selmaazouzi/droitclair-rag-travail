from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.session import get_session
from app.main import create_app


class FakeSession:
    def __init__(self, *, fail: bool) -> None:
        self._fail = fail

    async def execute(self, *_args: Any, **_kwargs: Any) -> None:
        if self._fail:
            raise ConnectionError("database is down")


def _client_with_session(session: FakeSession) -> AsyncClient:
    app = create_app()

    async def override() -> AsyncIterator[FakeSession]:
        yield session

    app.dependency_overrides[get_session] = override
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_health_ok() -> None:
    async with _client_with_session(FakeSession(fail=False)) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


async def test_health_reports_database_failure(caplog: pytest.LogCaptureFixture) -> None:
    async with _client_with_session(FakeSession(fail=True)) as client:
        response = await client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "error", "database": "error"}
    assert "database unreachable" in caplog.text
