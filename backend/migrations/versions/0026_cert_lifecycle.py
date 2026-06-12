"""revoked_syslog_certs ledger (tenant-scoped, RLS) + device_log_forwarding.revoked_at"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.db_roles import grant_app_role_statements
from app.core.rls import POLICY_NAME, policy_create_statement

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "device_log_forwarding",
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "revoked_syslog_certs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("serial", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute("ALTER TABLE revoked_syslog_certs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE revoked_syslog_certs FORCE ROW LEVEL SECURITY")
    op.execute(policy_create_statement("revoked_syslog_certs"))
    for stmt in grant_app_role_statements():
        op.execute(stmt)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON revoked_syslog_certs")
    op.execute("ALTER TABLE revoked_syslog_certs NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE revoked_syslog_certs DISABLE ROW LEVEL SECURITY")
    op.drop_table("revoked_syslog_certs")
    op.drop_column("device_log_forwarding", "revoked_at")
