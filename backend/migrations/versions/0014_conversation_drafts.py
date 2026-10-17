"""Preserve unsent conversation drafts."""
from alembic import op
import sqlalchemy as sa

revision = "0014_conversation_drafts"
down_revision = "0013_merge_discovery_folders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("draft_text", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("conversations", "draft_text")
