"""Initial document, page, and message tables."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("original_name", sa.String(512), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_documents_sha256", "documents", ["sha256"], unique=True)
    op.create_table(
        "pages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("width_pt", sa.Float(), nullable=False),
        sa.Column("height_pt", sa.Float(), nullable=False),
        sa.Column("rotation", sa.Integer(), nullable=False),
        sa.Column("native_text", sa.Text(), nullable=False),
        sa.UniqueConstraint("document_id", "page_number"),
    )
    op.create_index("ix_pages_document_id", "pages", ["document_id"])
    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("context_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_messages_document_id", "messages", ["document_id"])


def downgrade() -> None:
    op.drop_table("messages")
    op.drop_table("pages")
    op.drop_table("documents")
