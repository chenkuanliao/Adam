"""Add folder hierarchy and allow matching names in different parents."""
from alembic import op
import sqlalchemy as sa

revision = "0011_nested_folders"
down_revision = "0010_folder_colors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("folders", naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"}) as batch:
        batch.drop_constraint("uq_folders_name", type_="unique")
        batch.add_column(sa.Column("parent_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_folders_parent", "folders", ["parent_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_folders_parent_id", ["parent_id"])
    op.create_index("uq_folder_sibling_name", "folders", [sa.text("coalesce(parent_id, '')"), sa.text("lower(name)")], unique=True)


def downgrade() -> None:
    # Flattening cannot preserve duplicate names from different branches.
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT lower(name) FROM folders GROUP BY lower(name) HAVING count(*) > 1")).first():
        raise RuntimeError("Rename duplicate folder names before downgrading.")
    op.drop_index("uq_folder_sibling_name", table_name="folders")
    with op.batch_alter_table("folders") as batch:
        batch.drop_index("ix_folders_parent_id")
        batch.drop_constraint("fk_folders_parent", type_="foreignkey")
        batch.drop_column("parent_id")
        batch.create_unique_constraint("uq_folders_name", ["name"])
