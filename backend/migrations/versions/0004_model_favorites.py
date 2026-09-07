"""Store pinned provider models in SQLite."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_model_favorites"
down_revision: str | None = "0003_repair_annotations_table"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_favorites",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "model_id"),
    )
    op.create_index("ix_model_favorites_provider", "model_favorites", ["provider"])


def downgrade() -> None:
    op.drop_table("model_favorites")
