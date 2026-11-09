"""Local models through Ollama (``POST /api/chat``, streamed as one JSON object per line)."""

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from app.llm.base import ChatMessage, LLMChunk, LLMError, Usage


class OllamaClient:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        num_ctx: int = 16384,
        think: bool | None = False,
        timeout_s: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        # Ollama's default context window is small and it silently truncates the
        # prompt beyond it: always set it explicitly, large enough for the sources.
        self._num_ctx = num_ctx
        # Reasoning models (e.g. gemma4) "think" before answering by default: ~10x more
        # tokens and latency. False disables it; None leaves the model's default.
        self._think = think
        self._http = httpx.AsyncClient(
            base_url=self._base_url, timeout=timeout_s, transport=transport
        )

    @property
    def model(self) -> str:
        return self._model

    async def stream(
        self, messages: Sequence[ChatMessage], *, temperature: float
    ) -> AsyncIterator[LLMChunk]:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "options": {"temperature": temperature, "num_ctx": self._num_ctx},
        }
        if self._think is not None:
            body["think"] = self._think
        try:
            async with self._http.stream("POST", "/api/chat", json=body) as response:
                if response.status_code != 200:
                    detail = (await response.aread()).decode(errors="replace")[:300]
                    hint = (
                        f" (download it: ollama pull {self._model})"
                        if response.status_code == 404 and "not found" in detail
                        else ""
                    )
                    raise LLMError(f"Ollama HTTP {response.status_code}: {detail}{hint}")
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if "error" in data:
                        raise LLMError(f"Ollama: {data['error']}")
                    # Reasoning models also send a "thinking" field: it is not part of the answer.
                    text = (data.get("message") or {}).get("content") or ""
                    if data.get("done"):
                        usage = Usage(
                            prompt_tokens=data.get("prompt_eval_count"),
                            completion_tokens=data.get("eval_count"),
                        )
                        yield LLMChunk(text=text, usage=usage)
                    elif text:
                        yield LLMChunk(text=text)
        except httpx.ConnectError as exc:
            raise LLMError(
                f"Ollama is not reachable at {self._base_url} "
                "(start it: brew services start ollama)"
            ) from exc

    async def aclose(self) -> None:
        await self._http.aclose()
