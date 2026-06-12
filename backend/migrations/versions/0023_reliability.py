"""sweeper attempts (config_changes + firmware_actions) + config_changes.reverts_change_id"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("config_changes",
                  sa.Column("sweep_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("config_changes",
                  sa.Column("reverts_change_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_config_changes_reverts", "config_changes", "config_changes",
                          ["reverts_change_id"], ["id"], ondelete="SET NULL")
    op.add_column("firmware_actions",
                  sa.Column("sweep_attempts", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("firmware_actions", "sweep_attempts")
    op.drop_constraint("fk_config_changes_reverts", "config_changes", type_="foreignkey")
    op.drop_column("config_changes", "reverts_change_id")
    op.drop_column("config_changes", "sweep_attempts")
