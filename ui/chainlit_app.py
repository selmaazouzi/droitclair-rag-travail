"""Chainlit interface — a client of the API's ``POST /chat``, no business logic (ARCHITECTURE §3.1).

    uv run uvicorn app.main:app --reload                       # API on :8000
    cd ui && uv run chainlit run chainlit_app.py --port 8001    # interface on :8001

The API address can be changed with DROITCLAIR_API_URL.
"""

import os
from datetime import date
from typing import Any

import chainlit as cl
import httpx
from sse_client import aparse_sse  # Chainlit puts this file's folder on sys.path

API_URL = os.environ.get("DROITCLAIR_API_URL", "http://localhost:8000").rstrip("/")
TIMEOUT = httpx.Timeout(300.0, connect=5.0)


def _format_passages(passages: list[dict[str, Any]]) -> str:
    lines = []
    for p in passages:
        lines.append(
            f"**[{p['ref']}] {p['title']}** — similarité {p['score']:.3f}  \n"
            f"{p['excerpt']}{'…' if len(p['excerpt']) >= 300 else ''}"
        )
    return "\n\n".join(lines) or "Aucun passage trouvé."


def _format_sources(sources: list[dict[str, Any]]) -> str:
    if not sources:
        return ""
    lines = ["", "", "**Sources**"]
    for s in sources:
        updated = (
            f" — en vigueur depuis le {date.fromisoformat(s['updated']):%d/%m/%Y}"
            if s.get("updated")
            else ""
        )
        lines.append(f"- [{s['ref']}] [{s['title']}]({s['url']}){updated}")
    return "\n".join(lines)


def _format_footer(done: dict[str, Any], notice: str) -> str:
    warnings = []
    if done.get("invalid_citations"):
        warnings.append(f"⚠️ citations inexistantes retirées : {done['invalid_citations']}")
    if done.get("uncited_answer"):
        warnings.append("⚠️ réponse sans aucune citation")
    usage = done.get("usage") or {}
    stats = [done.get("model", ""), f"{done.get('latency_ms', 0) / 1000:.1f} s"]
    if usage.get("cost_usd") is not None:
        stats.append(f"{usage['cost_usd']:.5f} $")
    lines = [
        *(f"*{w}*" for w in warnings),
        f"*{notice}*" if notice else "",
        f"`{' · '.join(stats)}`",
    ]
    return "\n\n" + "\n\n".join(line for line in lines if line)


@cl.on_chat_start
async def on_chat_start() -> None:
    cl.user_session.set("conversation_id", None)


@cl.on_message
async def on_message(message: cl.Message) -> None:
    answer = cl.Message(content="")
    payload = {
        "conversation_id": cl.user_session.get("conversation_id"),
        "message": message.content,
    }
    sources: list[dict[str, Any]] = []
    notice = ""
    done: dict[str, Any] | None = None

    try:
        async with (
            httpx.AsyncClient(timeout=TIMEOUT) as client,
            client.stream("POST", f"{API_URL}/chat", json=payload) as response,
        ):
            if response.status_code != 200:
                detail = (await response.aread()).decode(errors="replace")[:300]
                await cl.Message(
                    content=f"⚠️ Erreur de l'API ({response.status_code}) : {detail}"
                ).send()
                return
            async for event in aparse_sse(response.aiter_lines()):
                match event.event:
                    case "meta":
                        cl.user_session.set("conversation_id", event.data["conversation_id"])
                    case "passages":
                        name = f"{len(event.data)} articles du Code trouvés"
                        async with cl.Step(name=name, type="retrieval") as step:
                            step.output = _format_passages(event.data)
                    case "token":
                        await answer.stream_token(event.data["text"])
                    case "sources":
                        sources = event.data
                    case "notice":
                        notice = event.data["text"]
                    case "done":
                        done = event.data
                    case "error":
                        await cl.Message(content=f"⚠️ {event.data['message']}").send()
                        return
    except httpx.ConnectError:
        await cl.Message(
            content=f"⚠️ L'API ne répond pas sur {API_URL}. "
            "Lance-la avec : `uv run uvicorn app.main:app --reload`"
        ).send()
        return

    if done is not None:
        # Final text from the API (invalid citations removed), then sources and footer.
        answer.content = done["answer"] + _format_sources(sources) + _format_footer(done, notice)
    await answer.send()  # also ends the token stream (Chainlit: "call .send() once streamed")
