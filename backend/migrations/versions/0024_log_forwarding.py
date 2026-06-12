"""syslog_ca (global) + device_log_forwarding (tenant-scoped, RLS)"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.db_roles import APP_ROLE, grant_app_role_statements
from app.core.rls import POLICY_NAME, policy_create_statement

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "syslog_ca",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("cert_pem", sa.Text(), nullable=False),
        sa.Column("key_enc", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="ck_syslog_ca_singleton"),
    )
    op.create_table(
        "device_log_forwarding",
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("cert_serial", sa.String(), nullable=False, server_default=""),
        sa.Column("cert_fingerprint", sa.String(), nullable=False, server_default=""),
        sa.Column("opnsense_ca_uuid", sa.String(), nullable=True),
        sa.Column("opnsense_cert_uuid", sa.String(), nullable=True),
        sa.Column("opnsense_dest_uuid", sa.String(), nullable=True),
        sa.Column("provisioned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("device_id"),
    )
    op.execute("ALTER TABLE device_log_forwarding ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE device_log_forwarding FORCE ROW LEVEL SECURITY")
    op.execute(policy_create_statement("device_log_forwarding"))
    for stmt in grant_app_role_statements():
        op.execute(stmt)


def downgrade() -> None:
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON device_log_forwarding FROM {APP_ROLE}")
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON device_log_forwarding")
    op.execute("ALTER TABLE device_log_forwarding NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE device_log_forwarding DISABLE ROW LEVEL SECURITY")
    op.drop_table("device_log_forwarding")
    op.drop_table("syslog_ca")
