"""Initial schema: extensions, French full-text config, documents, chunks, conversations.

Mirrors docs/ARCHITECTURE.md §8.

Revision ID: 0001
Revises:
Create Date: 2026-11-05
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 1024


def _uuid_pk() -> sa.Column[uuid.UUID]:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _now(name: str) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")

    # Accent-insensitive French full-text configuration: "période" and "periode" match.
    op.execute("CREATE TEXT SEARCH CONFIGURATION french_unaccent (COPY = french)")
    op.execute(
        "ALTER TEXT SEARCH CONFIGURATION french_unaccent "
        "ALTER MAPPING FOR hword, hword_part, word WITH unaccent, french_stem"
    )

    op.create_table(
        "documents",
        _uuid_pk(),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False, unique=True),
        sa.Column("article_num", sa.Text()),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("hierarchy_path", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("valid_from", sa.Date()),
        sa.Column("source_updated", sa.Date()),
        sa.Column("content_hash", sa.Text(), nullable=False),
        _now("ingested_at"),
    )
    op.create_index("ix_documents_article_num", "documents", ["article_num"])

    op.create_table(
        "chunks",
        _uuid_pk(),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(EMBEDDING_DIM), nullable=False),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('french_unaccent', content)", persisted=True),
        ),
        sa.UniqueConstraint("document_id", "chunk_index"),
    )
    op.create_index(
        "ix_chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index("ix_chunks_tsv", "chunks", ["tsv"], postgresql_using="gin")

    op.create_table(
        "conversations",
        _uuid_pk(),
        _now("created_at"),
    )

    op.create_table(
        "messages",
        _uuid_pk(),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.Text()),
        sa.Column("sources", postgresql.JSONB()),
        sa.Column("feedback", sa.SmallInteger()),
        sa.Column("feedback_comment", sa.Text()),
        _now("created_at"),
    )

    op.create_table(
        "ingestion_runs",
        _uuid_pk(),
        _now("started_at"),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("docs_added", sa.Integer()),
        sa.Column("docs_updated", sa.Integer()),
        sa.Column("docs_removed", sa.Integer()),
        sa.Column("docs_unchanged", sa.Integer()),
        sa.Column("index_version", sa.Text()),
    )


def downgrade() -> None:
    op.drop_table("ingestion_runs")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("chunks")
    op.drop_table("documents")
    op.execute("DROP TEXT SEARCH CONFIGURATION IF EXISTS french_unaccent")
    # Extensions are left in place: other objects in the database may depend on them.
