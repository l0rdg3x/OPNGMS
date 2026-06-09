"""config_snapshots table + RLS"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.db_roles import APP_ROLE, grant_app_role_statements
from app.core.rls import POLICY_NAME, policy_create_statement

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "config_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("taken_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("canonical_hash", sa.String(), nullable=False),
        sa.Column("content_enc", sa.LargeBinary(), nullable=False),
        sa.Column("opnsense_version", sa.String(), nullable=False, server_default=""),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_config_snapshots_tenant_id", "config_snapshots", ["tenant_id"])
    op.create_index("ix_config_snapshots_device_id", "config_snapshots", ["device_id"])
    op.create_index(
        "ix_config_snapshots_tenant_device_taken",
        "config_snapshots", ["tenant_id", "device_id", "taken_at"],
    )
    op.execute("ALTER TABLE config_snapshots ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE config_snapshots FORCE ROW LEVEL SECURITY")
    op.execute(policy_create_statement("config_snapshots"))
    for stmt in grant_app_role_statements():
        op.execute(stmt)


def downgrade() -> None:
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON config_snapshots FROM {APP_ROLE}")
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON config_snapshots")
    op.execute("ALTER TABLE config_snapshots NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE config_snapshots DISABLE ROW LEVEL SECURITY")
    op.drop_table("config_snapshots")
