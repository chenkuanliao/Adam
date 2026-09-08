"""Add one rich-text paper note per document."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_paper_notes"
down_revision: str | None = "0005_conversations"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_notes",
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("content_html", sa.Text(), nullable=False),
        sa.Column("plain_text", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("paper_notes")
