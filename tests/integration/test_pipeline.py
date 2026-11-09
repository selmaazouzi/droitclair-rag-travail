"""Question -> retrieval -> LLM -> citations, on a real PostgreSQL with fake models."""

from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.pipeline import Done, PipelineEvent, RetrievalDone, Token, answer_question
from app.prompts import load_prompt
from ingestion.documents import SourceDocument
from ingestion.embed import embed_pending
from ingestion.index import sync_documents
from tests.fakes import FakeEmbedder, FakeLLM


def article(num: str, body: str) -> SourceDocument:
    return SourceDocument(
        source_type="article",
        source_id=f"CID-{num}",
        title=f"Article {num}",
        hierarchy=("Partie législative",),
        url=f"https://www.legifrance.gouv.fr/codes/article_lc/ID-{num}",
        paragraphs=(body,),
        article_num=num,
        valid_from=date(2017, 9, 24),
    )


DOCS = [
    article("L1243-13-1", "Le contrat de travail à durée déterminée est renouvelable deux fois."),
    article("L1221-19", "La période d'essai des cadres est de quatre mois."),
    article(
        "L1237-13", "Chacune des parties dispose d'un délai de quinze jours pour se rétracter."
    ),
]
QUESTION = "Combien de fois un contrat à durée déterminée est-il renouvelable ?"


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE documents, ingestion_runs CASCADE"))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s, s.begin():
        await sync_documents(s, DOCS, source_types=["article"])
    await embed_pending(maker, FakeEmbedder())
    async with maker() as s:
        yield s


async def run(session: AsyncSession, llm: FakeLLM, top_k: int = 2) -> list[PipelineEvent]:
    return [
        event
        async for event in answer_question(
            QUESTION,
            session=session,
            embedder=FakeEmbedder(),
            llm=llm,
            prompt=load_prompt("system_v1"),
            top_k=top_k,
            temperature=0.1,
        )
    ]


async def test_answer_with_valid_and_invalid_citations(session: AsyncSession) -> None:
    llm = FakeLLM("Le CDD est renouvelable deux fois [1], voir aussi [5].")

    events = await run(session, llm)

    retrieval = events[0]
    assert isinstance(retrieval, RetrievalDone)
    assert retrieval.passages[0].article_num == "L1243-13-1"  # nearest first
    assert len(retrieval.passages) == 2  # top_k

    streamed = "".join(e.text for e in events if isinstance(e, Token))
    assert streamed == "Le CDD est renouvelable deux fois [1], voir aussi [5]."

    done = events[-1]
    assert isinstance(done, Done)
    result = done.result
    assert result.answer == "Le CDD est renouvelable deux fois [1], voir aussi."
    assert result.citations.cited == (1,) and result.citations.invalid == (5,)
    assert [(s.ref, s.article_num) for s in result.sources] == [(1, "L1243-13-1")]
    assert result.sources[0].updated == date(2017, 9, 24)
    assert result.usage is not None and result.usage.prompt_tokens == 100
    assert result.model == "fake-llm" and result.prompt_version == "system_v1"

    system, user = llm.received[0]
    assert system.role == "system" and system.content.startswith("Tu es un assistant")
    assert "[1] [Code du travail — Article L1243-13-1]" in user.content
    assert user.content.endswith(f"Question : {QUESTION}")


async def test_uncited_answer_is_flagged(session: AsyncSession) -> None:
    events = await run(session, FakeLLM("Une longue réponse sans aucune source citée. " * 10))
    done = events[-1]
    assert isinstance(done, Done)
    assert done.result.sources == () and done.result.citations.uncited_answer
