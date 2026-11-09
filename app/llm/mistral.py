"""Mistral API (``POST /v1/chat/completions``, streamed as server-sent events)."""

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from app.llm.base import ChatMessage, LLMChunk, LLMError, Usage

MISTRAL_API_URL = "https://api.mistral.ai/v1"


def _delta_text(content: Any) -> str:
    # Content is usually a string; some models send a list of typed parts.
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


class MistralClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = MISTRAL_API_URL,
        timeout_s: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._model = model
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_s,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}"},
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
        }
        async with self._http.stream("POST", "/chat/completions", json=body) as response:
            if response.status_code != 200:
                detail = (await response.aread()).decode(errors="replace")[:300]
                raise LLMError(f"Mistral HTTP {response.status_code}: {detail}")
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line.removeprefix("data:").strip()
                if payload == "[DONE]":
                    break
                data = json.loads(payload)
                choices = data.get("choices") or []
                text = (
                    _delta_text((choices[0].get("delta") or {}).get("content")) if choices else ""
                )
                usage = data.get("usage")
                if usage:
                    yield LLMChunk(
                        text=text,
                        usage=Usage(usage.get("prompt_tokens"), usage.get("completion_tokens")),
                    )
                elif text:
                    yield LLMChunk(text=text)

    async def aclose(self) -> None:
        await self._http.aclose()
