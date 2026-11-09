"""Incremental, idempotent indexing into PostgreSQL (ARCHITECTURE §9.4).

Compare each document's content hash with the database:
new -> insert document and chunks; changed -> replace them; gone from the
source -> delete. Unchanged documents are not touched, so re-running an
ingestion on the same data changes nothing.

Embeddings are not computed here: new or modified chunks are stored with a NULL
embedding, to be filled by the embedding step.
"""

import hashlib
import logging
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chunk as ChunkRow
from app.db.models import Document, IngestionRun
from ingestion.chunking import Chunk, chunk_document
from ingestion.documents import SourceDocument, SourceType

logger = logging.getLogger(__name__)

Chunker = Callable[[SourceDocument], list[Chunk]]

MIN_SOURCE_RATIO = 0.5
"""Refuse to sync if the source returns less than this share of what is indexed:
an API outage must not wipe the index."""


class SourceShrinkError(RuntimeError):
    pass


@dataclass
class SyncStats:
    added: int = 0
    updated: int = 0
    removed: int = 0
    unchanged: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated or self.removed)


def _document_values(doc: SourceDocument) -> dict[str, object]:
    return {
        "source_type": doc.source_type,
        "source_id": doc.source_id,
        "article_num": doc.article_num,
        "title": doc.title,
        "hierarchy_path": doc.hierarchy_path,
        "url": doc.url,
        "valid_from": doc.valid_from,
        "source_updated": doc.source_updated,
        "content_hash": doc.content_hash,
    }


def _chunk_rows(document_id: object, doc: SourceDocument, chunker: Chunker) -> list[ChunkRow]:
    return [
        ChunkRow(document_id=document_id, chunk_index=c.index, content=c.content, embedding=None)
        for c in chunker(doc)
    ]


async def sync_documents(
    session: AsyncSession,
    documents: Sequence[SourceDocument],
    *,
    source_types: Collection[SourceType],
    chunker: Chunker = chunk_document,
) -> SyncStats:
    """Bring the index for ``source_types`` in line with ``documents``. Does not commit."""
    unexpected = {d.source_type for d in documents} - set(source_types)
    if unexpected:
        raise ValueError(f"Documents of unexpected source types: {sorted(unexpected)}")
    if len({d.source_id for d in documents}) != len(documents):
        raise ValueError("Duplicate source_id in documents")

    rows = await session.execute(
        select(Document.source_id, Document.id, Document.content_hash).where(
            Document.source_type.in_(list(source_types))
        )
    )
    existing = {source_id: (doc_id, content_hash) for source_id, doc_id, content_hash in rows}
    if existing and len(documents) < MIN_SOURCE_RATIO * len(existing):
        raise SourceShrinkError(
            f"Source returned {len(documents)} documents but {len(existing)} are indexed "
            f"for {sorted(source_types)}; refusing to delete more than half of the index."
        )

    stats = SyncStats()
    for doc in documents:
        previous = existing.pop(doc.source_id, None)
        if previous is None:
            row = Document(**_document_values(doc))
            session.add(row)
            await session.flush()
            session.add_all(_chunk_rows(row.id, doc, chunker))
            stats.added += 1
        elif previous[1] != doc.content_hash:
            doc_id = previous[0]
            await session.execute(
                update(Document)
                .where(Document.id == doc_id)
                .values(**_document_values(doc), ingested_at=func.now())
            )
            await session.execute(delete(ChunkRow).where(ChunkRow.document_id == doc_id))
            session.add_all(_chunk_rows(doc_id, doc, chunker))
            stats.updated += 1
        else:
            stats.unchanged += 1

    if existing:
        removed_ids = [doc_id for doc_id, _ in existing.values()]
        await session.execute(delete(Document).where(Document.id.in_(removed_ids)))
        stats.removed = len(removed_ids)
        logger.info("Removed documents no longer in the source: %s", sorted(existing))

    await session.flush()
    return stats


async def compute_index_version(session: AsyncSession) -> str:
    """Deterministic fingerprint of the whole index: same content -> same version."""
    rows = await session.execute(
        select(Document.source_id, Document.content_hash).order_by(Document.source_id)
    )
    digest = hashlib.sha256()
    for source_id, content_hash in rows:
        digest.update(f"{source_id}:{content_hash}\n".encode())
    return digest.hexdigest()[:12]


async def record_run(session: AsyncSession, stats: SyncStats, started_at: datetime) -> IngestionRun:
    run = IngestionRun(
        started_at=started_at,
        finished_at=func.now(),
        docs_added=stats.added,
        docs_updated=stats.updated,
        docs_removed=stats.removed,
        docs_unchanged=stats.unchanged,
        index_version=await compute_index_version(session),
    )
    session.add(run)
    await session.flush()
    return run
