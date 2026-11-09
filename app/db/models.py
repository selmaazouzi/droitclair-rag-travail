"""ORM models mirroring the schema in docs/ARCHITECTURE.md §8.

The schema itself is owned by the Alembic migrations in `infra/alembic/`;
these models are used for querying and must stay in sync with them.
"""

import uuid
from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBEDDING_DIM = 1024
"""Dimension of BAAI/bge-m3 dense embeddings."""

TEXT_SEARCH_CONFIG = "french_unaccent"


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    source_type: Mapped[str] = mapped_column(Text)  # 'article' | 'fiche_sp' | 'fiche_travail'
    source_id: Mapped[str] = mapped_column(Text, unique=True)
    article_num: Mapped[str | None] = mapped_column(Text, index=True)
    title: Mapped[str] = mapped_column(Text)
    hierarchy_path: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    valid_from: Mapped[date | None] = mapped_column(Date)
    source_updated: Mapped[date | None] = mapped_column(Date)
    content_hash: Mapped[str] = mapped_column(Text)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index"),
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))  # NULL: to embed
    embedding_model: Mapped[str | None] = mapped_column(Text)  # model that produced it
    tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(f"to_tsvector('{TEXT_SEARCH_CONFIG}', content)", persisted=True),
    )

    document: Mapped[Document] = relationship(back_populates="chunks")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.created_at",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(Text)  # 'user' | 'assistant'
    content: Mapped[str] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(Text)
    sources: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    feedback: Mapped[int | None] = mapped_column(SmallInteger)  # 1, -1 or NULL
    feedback_comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    docs_added: Mapped[int | None] = mapped_column(Integer)
    docs_updated: Mapped[int | None] = mapped_column(Integer)
    docs_removed: Mapped[int | None] = mapped_column(Integer)
    docs_unchanged: Mapped[int | None] = mapped_column(Integer)
    index_version: Mapped[str | None] = mapped_column(Text)
