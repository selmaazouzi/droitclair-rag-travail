"""Async client for the Légifrance API (PISTE), see ARCHITECTURE §7 and §7.1.

- OAuth2 client-credentials token, refreshed before expiry or after a 401.
- Bounded concurrency and request rate (the API enforces quotas).
- Retries with exponential backoff on 429, 5xx and network errors.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import date
from types import TracebackType
from typing import Any, Self, cast

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
TOKEN_SAFETY_MARGIN_S = 60


class LegifranceError(RuntimeError):
    pass


class LegifranceClient:
    def __init__(
        self,
        *,
        api_url: str,
        oauth_url: str,
        client_id: str,
        client_secret: str,
        max_concurrency: int = 8,
        requests_per_second: float = 15.0,
        max_retries: int = 5,
        retry_base_delay_s: float = 0.5,
        timeout_s: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._token_url = f"{oauth_url.rstrip('/')}/api/oauth/token"
        self._client_id = client_id
        self._client_secret = client_secret
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._min_interval_s = 1.0 / requests_per_second
        self._max_retries = max_retries
        self._retry_base_delay_s = retry_base_delay_s
        self._sleep = sleep
        self._http = httpx.AsyncClient(timeout=timeout_s, transport=transport)
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()
        self._rate_lock = asyncio.Lock()
        self._next_slot = 0.0

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs: Any) -> Self:
        if not settings.legifrance_client_id or settings.legifrance_client_secret is None:
            raise LegifranceError(
                "LEGIFRANCE_CLIENT_ID / LEGIFRANCE_CLIENT_SECRET are not set (see .env.example)."
            )
        return cls(
            api_url=settings.legifrance_api_url,
            oauth_url=settings.legifrance_oauth_url,
            client_id=settings.legifrance_client_id,
            client_secret=settings.legifrance_client_secret.get_secret_value(),
            **kwargs,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._http.aclose()

    # --- Endpoints -------------------------------------------------------------

    async def table_of_contents(self, text_id: str, on: date) -> dict[str, Any]:
        """Structure of a code (sections and article ids, without article text)."""
        body = {"textId": text_id, "date": on.isoformat(), "nature": "CODE"}
        return await self._post("/consult/legi/tableMatieres", body)

    async def get_article(self, article_id: str) -> dict[str, Any]:
        """One article version, by its LEGIARTI version id."""
        payload = await self._post("/consult/getArticle", {"id": article_id})
        if not isinstance(payload.get("article"), dict):
            raise LegifranceError(f"No article in response for {article_id}")
        return payload

    # --- Plumbing --------------------------------------------------------------

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._api_url}{path}"
        async with self._semaphore:
            for attempt in range(self._max_retries + 1):
                await self._wait_for_rate_slot()
                try:
                    response = await self._http.post(
                        url,
                        json=body,
                        headers={"Authorization": f"Bearer {await self._get_token()}"},
                    )
                except httpx.TransportError as exc:
                    error: str = f"{type(exc).__name__}: {exc}"
                else:
                    if response.status_code == 200:
                        return cast(dict[str, Any], response.json())
                    if response.status_code == 401:
                        self._token = None  # expired or revoked: fetch a new one and retry
                    elif response.status_code not in RETRYABLE_STATUS:
                        raise LegifranceError(
                            f"POST {path} -> HTTP {response.status_code}: {response.text[:300]}"
                        )
                    error = f"HTTP {response.status_code}"

                if attempt == self._max_retries:
                    raise LegifranceError(
                        f"POST {path} failed after {attempt + 1} attempts: {error}"
                    )
                delay = self._retry_base_delay_s * 2**attempt
                logger.warning("POST %s: %s, retry %d in %.1fs", path, error, attempt + 1, delay)
                await self._sleep(delay)
        raise AssertionError("unreachable")

    async def _wait_for_rate_slot(self) -> None:
        async with self._rate_lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self._min_interval_s
        if slot > now:
            await self._sleep(slot - now)

    async def _get_token(self) -> str:
        async with self._token_lock:
            if self._token is None or time.monotonic() >= self._token_expires_at:
                response = await self._http.post(
                    self._token_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "scope": "openid",
                    },
                )
                if response.status_code != 200:
                    # The body never contains the secret; it is safe to surface.
                    raise LegifranceError(
                        f"OAuth token request failed: HTTP {response.status_code} "
                        f"{response.text[:200]}"
                    )
                payload = response.json()
                self._token = cast(str, payload["access_token"])
                expires_in = float(payload.get("expires_in", 3600))
                self._token_expires_at = time.monotonic() + expires_in - TOKEN_SAFETY_MARGIN_S
            return self._token
