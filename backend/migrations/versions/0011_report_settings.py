"""report_settings table + RLS"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.db_roles import APP_ROLE, grant_app_role_statements
from app.core.rls import POLICY_NAME, policy_create_statement

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_settings",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(), nullable=False, server_default="Security & Activity Report"),
        sa.Column("owner", sa.String(), nullable=False, server_default=""),
        sa.Column("timezone", sa.String(), nullable=False, server_default="UTC"),
        sa.Column("logo", sa.LargeBinary(), nullable=True),
        sa.Column("logo_mime", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id"),
    )
    op.execute("ALTER TABLE report_settings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE report_settings FORCE ROW LEVEL SECURITY")
    op.execute(policy_create_statement("report_settings"))
    for stmt in grant_app_role_statements():
        op.execute(stmt)


def downgrade() -> None:
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON report_settings FROM {APP_ROLE}")
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON report_settings")
    op.execute("ALTER TABLE report_settings NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE report_settings DISABLE ROW LEVEL SECURITY")
    op.drop_table("report_settings")
