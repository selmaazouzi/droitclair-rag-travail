"""LLM clients against fake HTTP servers: streaming formats, usage, errors. No network."""

import json

import httpx
import pytest

from app.config import Settings
from app.llm import ChatMessage, LLMChunk, LLMError, create_llm_client
from app.llm.ollama import OllamaClient
from app.llm.openrouter import OpenRouterClient

MESSAGES = [ChatMessage("system", "S"), ChatMessage("user", "Q")]


async def collect(client: OllamaClient | OpenRouterClient) -> list[LLMChunk]:
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


# --- OpenRouter: server-sent events ---------------------------------------------


def sse(*events: object) -> bytes:
    lines = [": OPENROUTER PROCESSING\n\n"]  # keep-alive comment, must be ignored
    lines += [f"data: {json.dumps(e)}\n\n" for e in events]
    return ("".join(lines) + "data: [DONE]\n\n").encode()


async def test_openrouter_streams_sse_with_usage_and_cost() -> None:
    body = sse(
        {"choices": [{"delta": {"role": "assistant", "content": "Deux "}}]},
        {"choices": [{"delta": {"content": [{"type": "text", "text": "fois"}]}}]},
        {"choices": [{"delta": {"content": " [1]."}}]},
        {
            "choices": [{"delta": {"content": ""}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1700, "completion_tokens": 300, "cost": 0.00026},
        },
    )
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=body)

    client = OpenRouterClient(
        api_key="sk-or-test",
        model="mistralai/mistral-small-3.2-24b-instruct",
        base_url="https://openrouter.test/api/v1",
        transport=httpx.MockTransport(handler),
    )
    chunks = await collect(client)

    assert "".join(c.text for c in chunks) == "Deux fois [1]."
    usage = chunks[-1].usage
    assert usage is not None and (usage.prompt_tokens, usage.completion_tokens) == (1700, 300)
    assert usage.cost_usd == 0.00026
    request = seen[0]
    assert request.url.path == "/api/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer sk-or-test"
    assert request.headers["X-Title"] == "DroitClair"
    sent = json.loads(request.content)
    assert sent["model"] == "mistralai/mistral-small-3.2-24b-instruct"
    assert sent["stream"] is True and sent["usage"] == {"include": True}


async def test_openrouter_error_after_stream_started() -> None:
    body = sse({"error": {"code": 502, "message": "Provider returned error"}})
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=body))
    client = OpenRouterClient(api_key="k", model="m", transport=transport)
    with pytest.raises(LLMError, match="Provider returned error"):
        await collect(client)


async def test_openrouter_http_error() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(401, text="No auth credentials found"))
    client = OpenRouterClient(api_key="bad", model="m", transport=transport)
    with pytest.raises(LLMError, match="HTTP 401"):
        await collect(client)


# --- Factory ------------------------------------------------------------------------


def test_factory_builds_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "gemma4:12b")
    client = create_llm_client(Settings(_env_file=None))  # type: ignore[call-arg]
    assert isinstance(client, OllamaClient) and client.model == "gemma4:12b"


def test_factory_builds_openrouter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    client = create_llm_client(Settings(_env_file=None))  # type: ignore[call-arg]
    assert isinstance(client, OpenRouterClient)
    assert client.model == "mistralai/mistral-small-3.2-24b-instruct"


def test_factory_requires_an_openrouter_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    with pytest.raises(LLMError, match="OPENROUTER_API_KEY"):
        create_llm_client(Settings(_env_file=None))  # type: ignore[call-arg]
