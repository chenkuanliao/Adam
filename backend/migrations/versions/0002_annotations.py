"""Add durable document annotations."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_annotations"
down_revision: str | None = "0001_initial"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "annotations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("selected_text", sa.Text(), nullable=False),
        sa.Column("color", sa.String(16), nullable=False),
        sa.Column("geometry_json", sa.Text(), nullable=False),
        sa.Column("note_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_annotations_document_id", "annotations", ["document_id"])


def downgrade() -> None:
    op.drop_table("annotations")
