"""Incremental indexing against a real PostgreSQL (ARCHITECTURE §9.4, phase 1 exit criteria)."""

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db.models import Chunk, Document, IngestionRun
from ingestion.documents import SourceDocument
from ingestion.index import SourceShrinkError, record_run, sync_documents


def article(num: str, *paragraphs: str) -> SourceDocument:
    return SourceDocument(
        source_type="article",
        source_id=f"CID-{num}",
        title=f"Article {num}",
        hierarchy=("Partie législative", "Livre II : Le contrat de travail"),
        url=f"https://www.legifrance.gouv.fr/codes/article_lc/ID-{num}",
        paragraphs=paragraphs or (f"Texte de l'article {num}.",),
        article_num=num,
        valid_from=date(2018, 1, 1),
    )


DOCS = [article("L1237-19"), article("L1221-19"), article("L1243-13")]


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE documents, ingestion_runs CASCADE"))
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session


async def sync(session: AsyncSession, docs: list[SourceDocument]) -> tuple[int, int, int, int]:
    stats = await sync_documents(session, docs, source_types=["article"])
    await record_run(session, stats, datetime.now(UTC))
    await session.commit()
    return stats.added, stats.updated, stats.removed, stats.unchanged


async def chunk_contents(session: AsyncSession, num: str) -> list[str]:
    rows = await session.scalars(
        select(Chunk.content)
        .join(Document)
        .where(Document.article_num == num)
        .order_by(Chunk.chunk_index)
    )
    return list(rows)


async def test_first_sync_inserts_documents_and_chunks(session: AsyncSession) -> None:
    assert await sync(session, DOCS) == (3, 0, 0, 0)

    contents = await chunk_contents(session, "L1237-19")
    assert len(contents) == 1
    assert contents[0].startswith("[Code du travail — Article L1237-19]")
    assert await session.scalar(select(func.count()).where(Chunk.embedding.is_(None))) == 3


async def test_second_sync_changes_nothing(session: AsyncSession) -> None:
    await sync(session, DOCS)
    ingested_at = await session.scalar(select(func.max(Document.ingested_at)))

    assert await sync(session, DOCS) == (0, 0, 0, 3)
    assert await session.scalar(select(func.max(Document.ingested_at))) == ingested_at

    versions = list(await session.scalars(select(IngestionRun.index_version)))
    assert len(versions) == 2 and versions[0] == versions[1]


async def test_modified_document_is_replaced(session: AsyncSession) -> None:
    await sync(session, DOCS)
    modified = [article("L1237-19", "Nouvel alinéa 1.", "Nouvel alinéa 2."), *DOCS[1:]]

    assert await sync(session, modified) == (0, 1, 0, 2)
    contents = await chunk_contents(session, "L1237-19")
    assert len(contents) == 1 and "Nouvel alinéa 2." in contents[0]


async def test_document_gone_from_source_is_removed_with_its_chunks(session: AsyncSession) -> None:
    await sync(session, DOCS)

    assert await sync(session, DOCS[:2]) == (0, 0, 1, 2)
    assert await chunk_contents(session, "L1243-13") == []
    assert await session.scalar(select(func.count()).select_from(Chunk)) == 2


async def test_index_version_tracks_content(session: AsyncSession) -> None:
    await sync(session, DOCS)
    await sync(session, [replace(DOCS[0], nota="Note ajoutée"), *DOCS[1:]])

    versions = list(await session.scalars(select(IngestionRun.index_version)))
    assert versions[0] != versions[1]


async def test_refuses_to_wipe_index_when_source_shrinks(session: AsyncSession) -> None:
    await sync(session, DOCS)

    with pytest.raises(SourceShrinkError):
        await sync_documents(session, DOCS[:1], source_types=["article"])
    await session.rollback()
    assert await session.scalar(select(func.count()).select_from(Document)) == 3


async def test_other_source_types_are_left_untouched(session: AsyncSession) -> None:
    await sync(session, DOCS)
    fiche = replace(DOCS[0], source_type="fiche_sp", source_id="F1", article_num=None)
    await sync_documents(session, [fiche], source_types=["fiche_sp"])
    await session.commit()

    # Re-syncing articles must not delete the fiche, and vice versa.
    assert await sync(session, DOCS) == (0, 0, 0, 3)
    assert await session.scalar(select(func.count()).select_from(Document)) == 4


async def test_rejects_documents_outside_declared_source_types(session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="unexpected source types"):
        await sync_documents(session, DOCS, source_types=["fiche_sp"])
