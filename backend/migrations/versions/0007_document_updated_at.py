"""Track document activity for the library dashboard."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_document_updated_at"
down_revision: str | None = "0006_paper_notes"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch_op:
        batch_op.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE documents SET updated_at = created_at WHERE updated_at IS NULL")
    with op.batch_alter_table("documents") as batch_op:
        batch_op.alter_column("updated_at", nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_column("updated_at")
