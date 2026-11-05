"""Record per-call token usage for the usage and cost dashboard."""
from alembic import op
import sqlalchemy as sa

revision = "0015_usage_events"
down_revision = "0014_conversation_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("uncached_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider_reported_cost_usd", sa.Float(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="completed"),
    )
    op.create_index("ix_usage_events_created_at", "usage_events", ["created_at"])
    op.create_index("ix_usage_events_provider", "usage_events", ["provider"])
    op.create_index("ix_usage_events_document_id", "usage_events", ["document_id"])
    op.create_index("ix_usage_events_conversation_id", "usage_events", ["conversation_id"])


def downgrade() -> None:
    op.drop_table("usage_events")
