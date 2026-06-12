"""silent_tenant_alerts: global MSP-level silent-tenant alert state (dedup + dashboard)"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.db_roles import grant_app_role_statements

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "silent_tenant_alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_name", sa.String(), nullable=False),
        sa.Column("silent_since", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_alert_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("details", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_silent_tenant_alerts_tenant"),
    )
    op.create_index("ix_silent_tenant_alerts_tenant_id", "silent_tenant_alerts", ["tenant_id"])
    # Global table (no RLS) — superadmin/worker only. Reapply the blanket app-role grants so
    # opngms_app can read/write it (matches smtp_settings/syslog_ca).
    for stmt in grant_app_role_statements():
        op.execute(stmt)


def downgrade() -> None:
    op.drop_index("ix_silent_tenant_alerts_tenant_id", table_name="silent_tenant_alerts")
    op.drop_table("silent_tenant_alerts")
