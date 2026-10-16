"""Persist folder icons while keeping the folder default for existing collections."""
from alembic import op
import sqlalchemy as sa

revision = "0011_folder_icons"
down_revision = "0010_folder_colors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("folders", sa.Column("icon", sa.String(24), nullable=False, server_default="folder"))


def downgrade() -> None:
    with op.batch_alter_table("folders") as batch:
        batch.drop_column("icon")
