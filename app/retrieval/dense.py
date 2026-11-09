"""Dense (vector) retrieval with pgvector (ARCHITECTURE §10, step 4)."""

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chunk, Document


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    content: str
    score: float  # cosine similarity, higher is closer
    article_num: str | None
    title: str
    url: str
    valid_from: date | None
    source_updated: date | None


async def dense_search(
    session: AsyncSession, query_vector: list[float], *, model_name: str, top_k: int
) -> list[RetrievedChunk]:
    """The ``top_k`` chunks closest to the query, among those embedded by ``model_name``.

    Vectors from different models are not comparable: chunks embedded by another
    model are ignored.
    """
    distance = Chunk.embedding.cosine_distance(query_vector)
    rows = await session.execute(
        select(
            Chunk.id,
            Chunk.content,
            (1 - distance).label("score"),
            Document.article_num,
            Document.title,
            Document.url,
            Document.valid_from,
            Document.source_updated,
        )
        .join(Document)
        .where(Chunk.embedding.is_not(None), Chunk.embedding_model == model_name)
        .order_by(distance)
        .limit(top_k)
    )
    return [
        RetrievedChunk(
            chunk_id=row.id,
            content=row.content,
            score=float(row.score),
            article_num=row.article_num,
            title=row.title,
            url=row.url,
            valid_from=row.valid_from,
            source_updated=row.source_updated,
        )
        for row in rows
    ]
