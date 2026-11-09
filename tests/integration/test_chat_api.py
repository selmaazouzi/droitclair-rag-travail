"""POST /chat end to end: SSE events and stored messages, on a real PostgreSQL, fake models."""

import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.chat import ChatServices, get_chat_services, get_chat_sessionmaker
from app.db.models import Message
from app.llm import ChatMessage, LLMChunk, LLMClient, LLMError
from app.main import create_app
from app.prompts import load_prompt
from ingestion.documents import SourceDocument
from ingestion.embed import embed_pending
from ingestion.index import sync_documents
from tests.fakes import FakeEmbedder, FakeLLM
from ui.sse_client import SSEEvent, parse_sse

DOCS = [
    SourceDocument(
        source_type="article",
        source_id=f"CID-{num}",
        title=f"Article {num}",
        hierarchy=("Partie législative",),
        url=f"https://www.legifrance.gouv.fr/codes/article_lc/ID-{num}",
        paragraphs=(body,),
        article_num=num,
        valid_from=date(2017, 9, 24),
    )
    for num, body in [
        ("L1243-13-1", "Le contrat de travail à durée déterminée est renouvelable deux fois."),
        ("L1221-19", "La période d'essai des cadres est de quatre mois."),
    ]
]
QUESTION = "Combien de fois un contrat à durée déterminée est-il renouvelable ?"


class BrokenLLM:
    model = "broken"

    async def stream(
        self, messages: Sequence[ChatMessage], *, temperature: float
    ) -> AsyncIterator[LLMChunk]:
        yield LLMChunk(text="Début")
        raise LLMError("Provider returned error")

    async def aclose(self) -> None:
        return None


@pytest.fixture
async def maker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE documents, ingestion_runs, conversations CASCADE"))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        await sync_documents(session, DOCS, source_types=["article"])
    await embed_pending(maker, FakeEmbedder())
    return maker


def client_for(maker: async_sessionmaker[AsyncSession], llm_factory: object) -> AsyncClient:
    app = create_app()
    app.dependency_overrides[get_chat_sessionmaker] = lambda: maker
    app.dependency_overrides[get_chat_services] = lambda: ChatServices(
        embedder=FakeEmbedder(),
        prompt=load_prompt("system_v1"),
        llm_factory=llm_factory,  # type: ignore[arg-type]
        top_k=2,
        temperature=0.1,
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def post_chat(client: AsyncClient, **body: object) -> list[SSEEvent]:
    response = await client.post("/chat", json=body)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return list(parse_sse(response.text.split("\n")))


async def test_chat_streams_events_and_stores_both_messages(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    def llm() -> LLMClient:
        return FakeLLM("Deux fois [1], voir aussi [9].")

    async with client_for(maker, llm) as client:
        events = await post_chat(client, message=QUESTION)

    names = [e.event for e in events]
    assert names[:2] == ["meta", "passages"]
    assert set(names[2:-3]) == {"token"}
    assert names[-3:] == ["sources", "notice", "done"]

    meta, passages, *_ = events
    assert passages.data[0]["article_num"] == "L1243-13-1"
    assert "renouvelable deux fois" in passages.data[0]["excerpt"]
    assert "".join(e.data["text"] for e in events if e.event == "token") == (
        "Deux fois [1], voir aussi [9]."
    )
    sources, notice, done = events[-3:]
    assert sources.data == [
        {
            "ref": 1,
            "title": "Article L1243-13-1",
            "article_num": "L1243-13-1",
            "url": "https://www.legifrance.gouv.fr/codes/article_lc/ID-L1243-13-1",
            "updated": "2017-09-24",
        }
    ]
    assert "pas un conseil personnalisé" in notice.data["text"]
    assert done.data["answer"] == "Deux fois [1], voir aussi."
    assert done.data["invalid_citations"] == [9]
    assert done.data["usage"]["prompt_tokens"] == 100

    async with maker() as session:
        rows = (await session.scalars(select(Message).order_by(Message.created_at))).all()
    assert [(m.role, m.content) for m in rows] == [
        ("user", QUESTION),
        ("assistant", "Deux fois [1], voir aussi."),
    ]
    assert str(rows[1].id) == meta.data["message_id"]
    assert str(rows[0].conversation_id) == meta.data["conversation_id"]
    assert rows[1].sources == sources.data


async def test_follow_up_question_reuses_the_conversation(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with client_for(maker, lambda: FakeLLM("Réponse [1].")) as client:
        first = await post_chat(client, message=QUESTION)
        conversation_id = first[0].data["conversation_id"]
        second = await post_chat(
            client, message="Et pour un CDI ?", conversation_id=conversation_id
        )

    assert second[0].data["conversation_id"] == conversation_id
    async with maker() as session:
        assert len((await session.scalars(select(Message))).all()) == 4


async def test_unknown_conversation_is_404(maker: async_sessionmaker[AsyncSession]) -> None:
    async with client_for(maker, lambda: FakeLLM("x")) as client:
        response = await client.post(
            "/chat", json={"message": QUESTION, "conversation_id": str(uuid.uuid4())}
        )
    assert response.status_code == 404


@pytest.mark.parametrize("message", ["", "x" * 2001])
async def test_invalid_question_is_422(
    maker: async_sessionmaker[AsyncSession], message: str
) -> None:
    async with client_for(maker, lambda: FakeLLM("x")) as client:
        response = await client.post("/chat", json={"message": message})
    assert response.status_code == 422


async def test_unconfigured_llm_sends_an_error_event(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    def missing_key() -> LLMClient:
        raise LLMError("OPENROUTER_API_KEY is not set")

    async with client_for(maker, missing_key) as client:
        events = await post_chat(client, message=QUESTION)

    assert [e.event for e in events] == ["meta", "error"]
    assert events[1].data == {
        "code": "llm_unavailable",
        "message": "OPENROUTER_API_KEY is not set",
    }


async def test_llm_failure_mid_stream_sends_an_error_event(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with client_for(maker, BrokenLLM) as client:
        events = await post_chat(client, message=QUESTION)

    assert events[-1].event == "error"
    assert events[-1].data["code"] == "llm_error"
    async with maker() as session:  # no assistant message stored for a failed answer
        roles = [m.role for m in (await session.scalars(select(Message))).all()]
    assert roles == ["user"]
