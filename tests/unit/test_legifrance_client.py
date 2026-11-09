"""LegifranceClient against a fake HTTP transport: no network, no credentials."""

import json
from collections.abc import Callable
from datetime import date

import httpx
import pytest

from app.config import Settings
from ingestion.sources.legifrance import LegifranceClient, LegifranceError

API = "https://api.test/lf"
OAUTH = "https://oauth.test"
TOKEN_URL = f"{OAUTH}/api/oauth/token"


class FakeApi:
    """Records requests.

    ``responses`` maps an API path to a list of (status, body), consumed in order.
    """

    def __init__(self, responses: dict[str, list[tuple[int, object]]]) -> None:
        self.responses = responses
        self.requests: list[httpx.Request] = []
        self.tokens_issued = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url) == TOKEN_URL:
            self.tokens_issued += 1
            return httpx.Response(
                200, json={"access_token": f"tok{self.tokens_issued}", "expires_in": 3600}
            )
        path = request.url.path.removeprefix("/lf")
        status, body = self.responses[path].pop(0)
        return httpx.Response(status, json=body)

    def api_calls(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == f"/lf{path}"]


def make_client(api: FakeApi, **kwargs: object) -> LegifranceClient:
    async def no_sleep(_: float) -> None:
        return None

    return LegifranceClient(
        api_url=API,
        oauth_url=OAUTH,
        client_id="id",
        client_secret="secret",
        transport=httpx.MockTransport(api),
        sleep=no_sleep,
        **kwargs,  # type: ignore[arg-type]
    )


ARTICLE = {"article": {"id": "LEGIARTI1", "num": "L1237-19"}}
Factory = Callable[[FakeApi], LegifranceClient]


async def test_token_is_fetched_once_and_sent_as_bearer() -> None:
    api = FakeApi({"/consult/getArticle": [(200, ARTICLE), (200, ARTICLE)]})
    async with make_client(api) as client:
        await client.get_article("LEGIARTI1")
        await client.get_article("LEGIARTI1")

    assert api.tokens_issued == 1
    calls = api.api_calls("/consult/getArticle")
    assert [c.headers["Authorization"] for c in calls] == ["Bearer tok1", "Bearer tok1"]
    assert json.loads(calls[0].content) == {"id": "LEGIARTI1"}


async def test_table_of_contents_request_body() -> None:
    api = FakeApi({"/consult/legi/tableMatieres": [(200, {"sections": []})]})
    async with make_client(api) as client:
        await client.table_of_contents("LEGITEXT000006072050", date(2026, 11, 5))

    body = json.loads(api.api_calls("/consult/legi/tableMatieres")[0].content)
    assert body == {"textId": "LEGITEXT000006072050", "date": "2026-11-05", "nature": "CODE"}


async def test_retries_on_server_errors_then_succeeds() -> None:
    api = FakeApi({"/consult/getArticle": [(500, {}), (429, {}), (200, ARTICLE)]})
    async with make_client(api) as client:
        assert await client.get_article("LEGIARTI1") == ARTICLE
    assert len(api.api_calls("/consult/getArticle")) == 3


async def test_gives_up_after_max_retries() -> None:
    api = FakeApi({"/consult/getArticle": [(503, {})] * 3})
    async with make_client(api, max_retries=2) as client:
        with pytest.raises(LegifranceError, match="after 3 attempts"):
            await client.get_article("LEGIARTI1")


async def test_refreshes_token_after_401() -> None:
    api = FakeApi({"/consult/getArticle": [(401, {}), (200, ARTICLE)]})
    async with make_client(api) as client:
        await client.get_article("LEGIARTI1")

    assert api.tokens_issued == 2
    auth = [c.headers["Authorization"] for c in api.api_calls("/consult/getArticle")]
    assert auth == ["Bearer tok1", "Bearer tok2"]


async def test_client_errors_are_not_retried() -> None:
    api = FakeApi({"/consult/getArticle": [(400, {"message": "bad id"})]})
    async with make_client(api) as client:
        with pytest.raises(LegifranceError, match="HTTP 400"):
            await client.get_article("bad")
    assert len(api.api_calls("/consult/getArticle")) == 1


async def test_response_without_article_is_an_error() -> None:
    api = FakeApi({"/consult/getArticle": [(200, {"executionTime": 1})]})
    async with make_client(api) as client:
        with pytest.raises(LegifranceError, match="No article"):
            await client.get_article("LEGIARTI1")


def test_from_settings_requires_credentials() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    with pytest.raises(LegifranceError, match="LEGIFRANCE_CLIENT_ID"):
        LegifranceClient.from_settings(settings)
