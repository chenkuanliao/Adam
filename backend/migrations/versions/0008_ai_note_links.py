"""add AI note links to annotations

Revision ID: 0008_ai_note_links
Revises: 0007_document_updated_at
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_ai_note_links"
down_revision = "0007_document_updated_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("annotations", sa.Column("ai_links_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("annotations", "ai_links_json")
