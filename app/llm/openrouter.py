"""OpenRouter (https://openrouter.ai): one API key, many hosted models.

OpenAI-compatible ``POST /chat/completions``, streamed as server-sent events. The
final event carries the token usage and the cost of the call.
"""

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from app.llm.base import ChatMessage, LLMChunk, LLMError, Usage

OPENROUTER_API_URL = "https://openrouter.ai/api/v1"
APP_TITLE = "DroitClair"  # shown in the OpenRouter dashboard (optional attribution header)


def _delta_text(content: Any) -> str:
    # Content is usually a string; some models send a list of typed parts.
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def _usage(data: dict[str, Any]) -> Usage:
    cost = data.get("cost")
    return Usage(
        prompt_tokens=data.get("prompt_tokens"),
        completion_tokens=data.get("completion_tokens"),
        cost_usd=float(cost) if cost is not None else None,
    )


class OpenRouterClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = OPENROUTER_API_URL,
        timeout_s: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._model = model
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_s,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}", "X-Title": APP_TITLE},
        )

    @property
    def model(self) -> str:
        return self._model

    async def stream(
        self, messages: Sequence[ChatMessage], *, temperature: float
    ) -> AsyncIterator[LLMChunk]:
        body = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "stream": True,
            "usage": {"include": True},  # token counts and cost in the final event
        }
        try:
            async with self._http.stream("POST", "/chat/completions", json=body) as response:
                if response.status_code != 200:
                    detail = (await response.aread()).decode(errors="replace")[:300]
                    raise LLMError(f"OpenRouter HTTP {response.status_code}: {detail}")
                # Lines starting with ":" are keep-alive comments; only "data:" lines matter.
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line.removeprefix("data:").strip()
                    if payload == "[DONE]":
                        break
                    data = json.loads(payload)
                    if "error" in data:  # errors after the stream has started
                        error = data["error"]
                        message = error.get("message") if isinstance(error, dict) else error
                        raise LLMError(f"OpenRouter: {message}")
                    choices = data.get("choices") or []
                    delta = (choices[0].get("delta") or {}) if choices else {}
                    text = _delta_text(delta.get("content"))
                    if data.get("usage"):
                        yield LLMChunk(text=text, usage=_usage(data["usage"]))
                    elif text:
                        yield LLMChunk(text=text)
        except httpx.ConnectError as exc:
            raise LLMError("OpenRouter is not reachable (network?)") from exc

    async def aclose(self) -> None:
        await self._http.aclose()
