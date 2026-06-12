"""report delivery: smtp_settings + report_schedule (+RLS) + report_settings.from_email
and generated_reports.device_id"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.db_roles import APP_ROLE, grant_app_role_statements
from app.core.rls import POLICY_NAME, policy_create_statement

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "smtp_settings",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("host", sa.String(), nullable=False, server_default=""),
        sa.Column("port", sa.Integer(), nullable=False, server_default="587"),
        sa.Column("security", sa.String(), nullable=False, server_default="starttls"),
        sa.Column("username", sa.String(), nullable=True),
        sa.Column("password_enc", sa.LargeBinary(), nullable=True),
        sa.Column("from_email", sa.String(), nullable=False, server_default=""),
        sa.Column("from_name", sa.String(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="ck_smtp_settings_singleton"),
    )

    op.create_table(
        "report_schedule",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("frequency", sa.String(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=True),
        sa.Column("hour", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("recipients", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("hour BETWEEN 0 AND 23", name="ck_report_schedule_hour"),
        sa.CheckConstraint("weekday IS NULL OR weekday BETWEEN 0 AND 6", name="ck_report_schedule_weekday"),
    )
    op.create_index("uq_report_schedule_tenant", "report_schedule", ["tenant_id"], unique=True,
                    postgresql_where=sa.text("device_id IS NULL"))
    op.create_index("uq_report_schedule_device", "report_schedule", ["tenant_id", "device_id"], unique=True,
                    postgresql_where=sa.text("device_id IS NOT NULL"))
    op.create_index("ix_report_schedule_due", "report_schedule", ["enabled", "next_run_at"])
    op.execute("ALTER TABLE report_schedule ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE report_schedule FORCE ROW LEVEL SECURITY")
    op.execute(policy_create_statement("report_schedule"))

    op.add_column("report_settings", sa.Column("from_email", sa.String(), nullable=False, server_default=""))
    op.add_column("generated_reports", sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_generated_reports_device", "generated_reports", "devices",
                          ["device_id"], ["id"], ondelete="SET NULL")

    for stmt in grant_app_role_statements():
        op.execute(stmt)


def downgrade() -> None:
    op.drop_constraint("fk_generated_reports_device", "generated_reports", type_="foreignkey")
    op.drop_column("generated_reports", "device_id")
    op.drop_column("report_settings", "from_email")
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON report_schedule FROM {APP_ROLE}")
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON report_schedule")
    op.execute("ALTER TABLE report_schedule NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE report_schedule DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_report_schedule_due", table_name="report_schedule")
    op.drop_index("uq_report_schedule_device", table_name="report_schedule")
    op.drop_index("uq_report_schedule_tenant", table_name="report_schedule")
    op.drop_table("report_schedule")
    op.drop_table("smtp_settings")
