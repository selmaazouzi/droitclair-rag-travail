"""Make chunks.embedding nullable: chunks are stored first, embeddings computed afterwards.

Only chunks whose embedding is NULL (new or modified) are embedded (ARCHITECTURE §9.4).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 1024


def upgrade() -> None:
    op.alter_column(
        "chunks",
        "embedding",
        existing_type=pgvector.sqlalchemy.Vector(EMBEDDING_DIM),
        nullable=True,
    )


def downgrade() -> None:
    op.execute("DELETE FROM chunks WHERE embedding IS NULL")
    op.alter_column(
        "chunks",
        "embedding",
        existing_type=pgvector.sqlalchemy.Vector(EMBEDDING_DIM),
        nullable=False,
    )
