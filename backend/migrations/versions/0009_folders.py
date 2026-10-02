"""Add optional library folders.

Revision ID: 0009_folders
Revises: 0008_ai_note_links
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_folders"
down_revision = "0008_ai_note_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("folders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("folder_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_documents_folder", "folders", ["folder_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_documents_folder_id", ["folder_id"])


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_index("ix_documents_folder_id")
        batch.drop_constraint("fk_documents_folder", type_="foreignkey")
        batch.drop_column("folder_id")
    op.drop_table("folders")
