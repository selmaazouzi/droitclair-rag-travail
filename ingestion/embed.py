"""Fill missing embeddings (ARCHITECTURE §9.4).

A chunk needs an embedding when it has none (new or modified chunk, see index.py)
or when it was embedded by another model than the configured one. Vectors are
committed batch by batch: an interrupted run resumes where it stopped.
"""

import asyncio
import itertools
import logging
from dataclasses import dataclass

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import EMBEDDING_DIM, Chunk
from app.embeddings import Embedder

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EmbedSummary:
    embedded: int
    already_done: int
    max_tokens: int  # longest chunk embedded in this run
    truncated: int  # chunks longer than the model's input limit


async def embed_pending(
    sessionmaker: async_sessionmaker[AsyncSession],
    embedder: Embedder,
    *,
    batch_size: int = 32,
) -> EmbedSummary:
    if embedder.dimension != EMBEDDING_DIM:
        raise ValueError(
            f"{embedder.model_name} produces {embedder.dimension}-dimensional vectors, "
            f"the database expects {EMBEDDING_DIM}."
        )

    async with sessionmaker() as session:
        pending = (
            await session.execute(
                select(Chunk.id, Chunk.content)
                .where(
                    or_(
                        Chunk.embedding.is_(None),
                        Chunk.embedding_model.is_distinct_from(embedder.model_name),
                    )
                )
                .order_by(Chunk.id)
            )
        ).all()
        total = await session.scalar(select(func.count()).select_from(Chunk)) or 0

    token_counts = [embedder.count_tokens(content) for _, content in pending]
    truncated = sum(1 for n in token_counts if n > embedder.max_input_tokens)
    if truncated:
        logger.warning(
            "%d chunks exceed %d tokens and will be truncated by the model",
            truncated,
            embedder.max_input_tokens,
        )

    done = 0
    for batch in itertools.batched(pending, batch_size):
        texts = [content for _, content in batch]
        vectors = await asyncio.to_thread(embedder.embed_documents, texts)
        async with sessionmaker() as session, session.begin():
            for (chunk_id, _), vector in zip(batch, vectors, strict=True):
                await session.execute(
                    update(Chunk)
                    .where(Chunk.id == chunk_id)
                    .values(embedding=vector, embedding_model=embedder.model_name)
                )
        done += len(batch)
        logger.info("Embedded %d/%d chunks", done, len(pending))

    return EmbedSummary(
        embedded=len(pending),
        already_done=total - len(pending),
        max_tokens=max(token_counts, default=0),
        truncated=truncated,
    )
