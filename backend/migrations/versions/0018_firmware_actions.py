"""firmware_actions table + RLS"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.db_roles import APP_ROLE, grant_app_role_statements
from app.core.rls import POLICY_NAME, policy_create_statement

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "firmware_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=False, server_default=""),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="scheduled"),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_firmware_actions_tenant_id", "firmware_actions", ["tenant_id"])
    op.create_index("ix_firmware_actions_device_id", "firmware_actions", ["device_id"])
    op.create_index(
        "ix_firmware_actions_tenant_device_created",
        "firmware_actions", ["tenant_id", "device_id", "created_at"],
    )
    op.execute("ALTER TABLE firmware_actions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE firmware_actions FORCE ROW LEVEL SECURITY")
    op.execute(policy_create_statement("firmware_actions"))
    for stmt in grant_app_role_statements():
        op.execute(stmt)


def downgrade() -> None:
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON firmware_actions FROM {APP_ROLE}")
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON firmware_actions")
    op.execute("ALTER TABLE firmware_actions NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE firmware_actions DISABLE ROW LEVEL SECURITY")
    op.drop_table("firmware_actions")
