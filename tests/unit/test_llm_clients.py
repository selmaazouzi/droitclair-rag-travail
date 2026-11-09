"""LLM clients against fake HTTP servers: streaming formats, usage, errors. No network."""

import json

import httpx
import pytest

from app.config import Settings
from app.llm import ChatMessage, LLMChunk, LLMError, create_llm_client
from app.llm.mistral import MistralClient
from app.llm.ollama import OllamaClient

MESSAGES = [ChatMessage("system", "S"), ChatMessage("user", "Q")]


async def collect(client: OllamaClient | MistralClient) -> list[LLMChunk]:
    try:
        return [chunk async for chunk in client.stream(MESSAGES, temperature=0.1)]
    finally:
        await client.aclose()


# --- Ollama: one JSON object per line -------------------------------------------


def ollama_server(lines: list[dict[str, object]], seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())

    return httpx.MockTransport(handler)


async def test_ollama_streams_text_then_usage() -> None:
    seen: list[httpx.Request] = []
    lines: list[dict[str, object]] = [
        {"message": {"role": "assistant", "content": "Deux "}, "done": False},
        {"message": {"role": "assistant", "content": "", "thinking": "hmm"}, "done": False},
        {"message": {"role": "assistant", "content": "fois [1]."}, "done": False},
        {"message": {"content": ""}, "done": True, "prompt_eval_count": 812, "eval_count": 9},
    ]
    client = OllamaClient(
        base_url="http://ollama.test",
        model="gemma4:12b",
        num_ctx=16384,
        transport=ollama_server(lines, seen),
    )

    chunks = await collect(client)

    assert "".join(c.text for c in chunks) == "Deux fois [1]."
    assert chunks[-1].usage is not None and chunks[-1].usage.prompt_tokens == 812
    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/api/chat"
    assert body["model"] == "gemma4:12b" and body["stream"] is True
    assert body["options"] == {"temperature": 0.1, "num_ctx": 16384}
    assert body["think"] is False  # reasoning disabled by default (latency)
    assert body["messages"] == [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "Q"},
    ]


async def test_ollama_error_in_stream() -> None:
    client = OllamaClient(
        base_url="http://ollama.test",
        model="m",
        transport=ollama_server([{"error": "model 'm' not found"}], []),
    )
    with pytest.raises(LLMError, match="not found"):
        await collect(client)


async def test_ollama_missing_model_says_how_to_download_it() -> None:
    body = {"error": "model 'gemma4:12b' not found"}
    transport = httpx.MockTransport(lambda r: httpx.Response(404, json=body))
    client = OllamaClient(base_url="http://ollama.test", model="gemma4:12b", transport=transport)
    with pytest.raises(LLMError, match=r"HTTP 404.*ollama pull gemma4:12b"):
        await collect(client)


async def test_ollama_not_running_gives_a_hint() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = OllamaClient(
        base_url="http://ollama.test", model="m", transport=httpx.MockTransport(refuse)
    )
    with pytest.raises(LLMError, match="brew services start ollama"):
        await collect(client)


# --- Mistral: server-sent events ------------------------------------------------


async def test_mistral_streams_sse_with_usage() -> None:
    events = [
        {"choices": [{"delta": {"role": "assistant", "content": "Deux "}}]},
        {"choices": [{"delta": {"content": [{"type": "text", "text": "fois"}]}}]},
        {
            "choices": [{"delta": {"content": " [1]."}}],
            "usage": {"prompt_tokens": 700, "completion_tokens": 5},
        },
    ]
    sse = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=sse.encode())

    client = MistralClient(
        api_key="k", model="mistral-small-latest", transport=httpx.MockTransport(handler)
    )
    chunks = await collect(client)

    assert "".join(c.text for c in chunks) == "Deux fois [1]."
    assert chunks[-1].usage is not None and chunks[-1].usage.completion_tokens == 5
    assert seen[0].headers["Authorization"] == "Bearer k"
    assert seen[0].url.path == "/v1/chat/completions"


async def test_mistral_http_error() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(401, text="Unauthorized"))
    client = MistralClient(api_key="bad", model="m", transport=transport)
    with pytest.raises(LLMError, match="HTTP 401"):
        await collect(client)


# --- Factory ------------------------------------------------------------------------


def test_factory_builds_the_configured_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MODEL", "gemma4:12b")
    client = create_llm_client(Settings(_env_file=None))  # type: ignore[call-arg]
    assert isinstance(client, OllamaClient) and client.model == "gemma4:12b"


def test_factory_requires_a_mistral_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mistral")
    with pytest.raises(LLMError, match="MISTRAL_API_KEY"):
        create_llm_client(Settings(_env_file=None))  # type: ignore[call-arg]
