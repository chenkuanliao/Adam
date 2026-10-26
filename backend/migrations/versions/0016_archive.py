"""Let folders and papers be archived and restored."""
from alembic import op
import sqlalchemy as sa

revision = "0016_archive"
down_revision = "0015_usage_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("folders", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_column("archived_at")
    with op.batch_alter_table("folders") as batch:
        batch.drop_column("archived_at")
