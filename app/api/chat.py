"""POST /chat — answer a question as Server-Sent Events (ARCHITECTURE §11).

Events, in order: ``meta``, ``passages``, ``token`` (repeated), ``sources``, ``notice``,
``done``; or ``error`` at any point. Each event is ``event: <name>`` then
``data: <json>``. The question and the answer are stored in ``conversations`` /
``messages``, so that feedback (phase 7) can be attached to an answer.

Thin layer over ``app.pipeline``: no retrieval or prompting logic here.
"""

import functools
import json
import logging
import threading
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.db.models import Conversation, Message
from app.db.session import get_sessionmaker
from app.embeddings import Embedder, SentenceTransformerEmbedder
from app.llm import LLMClient, LLMError, create_llm_client
from app.pipeline import AnswerResult, Done, RetrievalDone, Token, answer_question
from app.prompts import Prompt, load_prompt
from app.retrieval.dense import RetrievedChunk

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

MAX_QUESTION_CHARS = 2000


class ChatRequest(BaseModel):
    conversation_id: uuid.UUID | None = None
    message: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)


@dataclass(frozen=True, slots=True)
class ChatServices:
    embedder: Embedder
    prompt: Prompt
    llm_factory: Callable[[], LLMClient]  # one client per request
    top_k: int
    temperature: float


_services_lock = threading.Lock()


@functools.cache
def _build_services() -> ChatServices:
    settings = get_settings()
    return ChatServices(
        embedder=SentenceTransformerEmbedder(
            settings.embedding_model, device=settings.embedding_device
        ),
        prompt=load_prompt(settings.prompt_system_version),
        llm_factory=functools.partial(create_llm_client, settings),
        top_k=settings.rerank_top_n,
        temperature=settings.llm_temperature,
    )


def get_chat_services() -> ChatServices:
    """Loaded on the first question (the embedding model takes a few seconds), then reused."""
    with _services_lock:
        return _build_services()


def get_chat_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return get_sessionmaker()


def _sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _passage_payload(ref: int, passage: RetrievedChunk) -> dict[str, Any]:
    body = passage.content.split("\n", 2)[-1]  # without the 2-line header
    return {
        "ref": ref,
        "title": passage.title,
        "article_num": passage.article_num,
        "url": passage.url,
        "score": round(passage.score, 4),
        "excerpt": body[:300],
    }


def _sources_payload(result: AnswerResult) -> list[dict[str, Any]]:
    return [
        {
            "ref": s.ref,
            "title": s.title,
            "article_num": s.article_num,
            "url": s.url,
            "updated": s.updated.isoformat() if s.updated else None,
        }
        for s in result.sources
    ]


def _done_payload(result: AnswerResult) -> dict[str, Any]:
    usage = result.usage
    return {
        "latency_ms": result.latency_ms,
        "usage": None
        if usage is None
        else {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "cost_usd": usage.cost_usd,
        },
        "model": result.model,
        "prompt_version": result.prompt_version,
        "answer": result.answer,  # final text: invalid citations removed
        "invalid_citations": list(result.citations.invalid),
        "uncited_answer": result.citations.uncited_answer,
    }


async def _events(
    question: str,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    services: ChatServices,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[str]:
    yield _sse(
        "meta",
        {"conversation_id": str(conversation_id), "message_id": str(message_id), "trace_id": None},
    )
    try:
        llm = services.llm_factory()
    except LLMError as exc:
        yield _sse("error", {"code": "llm_unavailable", "message": str(exc)})
        return

    try:
        result: AnswerResult | None = None
        async with sessionmaker() as session:
            async for event in answer_question(
                question,
                session=session,
                embedder=services.embedder,
                llm=llm,
                prompt=services.prompt,
                top_k=services.top_k,
                temperature=services.temperature,
            ):
                if isinstance(event, RetrievalDone):
                    passages = [_passage_payload(i, p) for i, p in enumerate(event.passages, 1)]
                    yield _sse("passages", passages)
                elif isinstance(event, Token):
                    yield _sse("token", {"text": event.text})
                elif isinstance(event, Done):
                    result = event.result
        if result is None:  # pragma: no cover - the pipeline always ends with Done
            raise RuntimeError("pipeline ended without a result")

        sources = _sources_payload(result)
        yield _sse("sources", sources)
        yield _sse("notice", {"text": result.notice})
        async with sessionmaker() as session, session.begin():
            session.add(
                Message(
                    id=message_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=result.answer,
                    sources=sources,
                )
            )
        yield _sse("done", _done_payload(result))
    except LLMError as exc:
        yield _sse("error", {"code": "llm_error", "message": str(exc)})
    except Exception:
        logger.exception("Unexpected error while answering")
        yield _sse("error", {"code": "internal_error", "message": "Erreur interne du serveur."})
    finally:
        await llm.aclose()


@router.post(
    "/chat",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}, 404: {"description": "Unknown id"}},
)
async def chat(
    body: ChatRequest,
    services: Annotated[ChatServices, Depends(get_chat_services)],
    sessionmaker: Annotated[async_sessionmaker[AsyncSession], Depends(get_chat_sessionmaker)],
) -> StreamingResponse:
    async with sessionmaker() as session, session.begin():
        if body.conversation_id is None:
            conversation = Conversation()
            session.add(conversation)
            await session.flush()
            conversation_id = conversation.id
        elif await session.get(Conversation, body.conversation_id) is None:
            raise HTTPException(status_code=404, detail="Conversation inconnue.")
        else:
            conversation_id = body.conversation_id
        session.add(Message(conversation_id=conversation_id, role="user", content=body.message))

    return StreamingResponse(
        _events(body.message, conversation_id, uuid.uuid4(), services, sessionmaker),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
