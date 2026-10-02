"""Persist folder colors, with sage as the default for existing folders."""
from alembic import op
import sqlalchemy as sa

revision = "0010_folder_colors"
down_revision = "0009_folders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("folders", sa.Column("color", sa.String(7), nullable=False, server_default="#8c9d65"))


def downgrade() -> None:
    with op.batch_alter_table("folders") as batch:
        batch.drop_column("color")
