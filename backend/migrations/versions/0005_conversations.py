"""Add durable per-paper conversations."""

from collections.abc import Sequence
from datetime import datetime, timezone
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0005_conversations"
down_revision: str | None = "0004_model_favorites"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("context_builder_version", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_conversations_document_id", "conversations", ["document_id"])
    with op.batch_alter_table("messages") as batch:
        batch.add_column(sa.Column("conversation_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_messages_conversation", "conversations", ["conversation_id"], ["id"], ondelete="CASCADE")
        batch.create_index("ix_messages_conversation_id", ["conversation_id"])

    connection = op.get_bind()
    now = datetime.now(timezone.utc)
    document_ids = [row[0] for row in connection.execute(sa.text("SELECT DISTINCT document_id FROM messages WHERE conversation_id IS NULL"))]
    for document_id in document_ids:
        conversation_id = str(uuid4())
        connection.execute(sa.text("""INSERT INTO conversations
            (id, document_id, title, provider, model_id, system_prompt, context_builder_version, created_at, updated_at)
            VALUES (:id, :document_id, 'Previous chat', 'zen', 'gemini-3.8-flash', '', 'legacy', :created_at, :updated_at)"""),
            {"id": conversation_id, "document_id": document_id, "created_at": now, "updated_at": now})
        connection.execute(sa.text("UPDATE messages SET conversation_id = :conversation_id WHERE document_id = :document_id AND conversation_id IS NULL"),
                           {"conversation_id": conversation_id, "document_id": document_id})


def downgrade() -> None:
    with op.batch_alter_table("messages") as batch:
        batch.drop_index("ix_messages_conversation_id")
        batch.drop_constraint("fk_messages_conversation", type_="foreignkey")
        batch.drop_column("conversation_id")
    op.drop_index("ix_conversations_document_id", table_name="conversations")
    op.drop_table("conversations")
