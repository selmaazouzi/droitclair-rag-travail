"""Embedding step against a real PostgreSQL + pgvector, with a fake model."""

from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db.models import Chunk, Document
from ingestion.documents import SourceDocument
from ingestion.embed import embed_pending
from ingestion.index import sync_documents
from tests.fakes import FakeEmbedder


def article(num: str, body: str) -> SourceDocument:
    return SourceDocument(
        source_type="article",
        source_id=f"CID-{num}",
        title=f"Article {num}",
        hierarchy=("Partie législative",),
        url=f"https://www.legifrance.gouv.fr/codes/article_lc/ID-{num}",
        paragraphs=(body,),
        article_num=num,
        valid_from=date(2018, 1, 1),
    )


DOCS = [
    article("L1221-19", "La période d'essai des cadres dure au maximum quatre mois."),
    article("L1243-13", "Le contrat à durée déterminée peut être renouvelé deux fois."),
    article("L1237-19", "La rupture conventionnelle collective exclut tout licenciement."),
]


@pytest.fixture
async def sessionmaker(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE documents, ingestion_runs CASCADE"))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        await sync_documents(session, DOCS, source_types=["article"])
    yield maker


async def count(maker: async_sessionmaker[AsyncSession], *where: object) -> int:
    async with maker() as session:
        return await session.scalar(select(func.count()).select_from(Chunk).where(*where)) or 0


async def test_embeds_every_pending_chunk_once(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    embedder = FakeEmbedder()

    first = await embed_pending(sessionmaker, embedder, batch_size=2)
    second = await embed_pending(sessionmaker, embedder, batch_size=2)

    assert (first.embedded, first.already_done) == (3, 0)
    assert (second.embedded, second.already_done) == (0, 3)
    assert [len(batch) for batch in embedder.calls] == [2, 1]  # batches, nothing re-embedded
    assert await count(sessionmaker, Chunk.embedding.is_(None)) == 0
    assert await count(sessionmaker, Chunk.embedding_model == "fake-bow") == 3


async def test_only_modified_chunks_are_re_embedded(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await embed_pending(sessionmaker, FakeEmbedder())
    modified = [article("L1221-19", "Nouvelle rédaction de l'article."), *DOCS[1:]]
    async with sessionmaker() as session, session.begin():
        await sync_documents(session, modified, source_types=["article"])

    summary = await embed_pending(sessionmaker, FakeEmbedder())

    assert (summary.embedded, summary.already_done) == (1, 2)


async def test_changing_the_model_re_embeds_everything(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await embed_pending(sessionmaker, FakeEmbedder(model_name="model-a"))

    summary = await embed_pending(sessionmaker, FakeEmbedder(model_name="model-b"))

    assert summary.embedded == 3
    assert await count(sessionmaker, Chunk.embedding_model == "model-b") == 3


async def test_nearest_chunk_by_cosine_distance(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    embedder = FakeEmbedder()
    await embed_pending(sessionmaker, embedder)
    question = embedder.embed_query("durée maximale de la période d'essai des cadres")

    async with sessionmaker() as session:
        nearest = await session.scalar(
            select(Document.article_num)
            .join(Chunk)
            .order_by(Chunk.embedding.cosine_distance(question))
            .limit(1)
        )

    assert nearest == "L1221-19"


async def test_reports_chunks_longer_than_the_model_limit(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    summary = await embed_pending(sessionmaker, FakeEmbedder(max_input_tokens=10))
    assert summary.truncated == 3
    assert summary.max_tokens > 10


async def test_rejects_a_model_with_the_wrong_dimension(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(ValueError, match="1024"):
        await embed_pending(sessionmaker, FakeEmbedder(dimension=384))
