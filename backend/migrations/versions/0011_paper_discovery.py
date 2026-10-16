"""Keep discovery provenance with downloaded papers."""
from alembic import op
import sqlalchemy as sa

revision = "0011_paper_discovery"
down_revision = "0010_folder_colors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("discovery_json", sa.Text(), nullable=False, server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_column("discovery_json")
