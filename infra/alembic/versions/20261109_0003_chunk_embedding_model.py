"""Record which model produced each chunk embedding.

Changing EMBEDDING_MODEL then re-embeds every chunk instead of mixing vectors
from two models, which are not comparable.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chunks", sa.Column("embedding_model", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("chunks", "embedding_model")
