"""generated_reports table + RLS"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.db_roles import APP_ROLE, grant_app_role_statements
from app.core.rls import POLICY_NAME, policy_create_statement

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generated_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("period_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pdf", sa.LargeBinary(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_generated_reports_tenant_id", "generated_reports", ["tenant_id"])
    op.create_index("ix_generated_reports_tenant_created", "generated_reports", ["tenant_id", "created_at"])
    op.execute("ALTER TABLE generated_reports ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE generated_reports FORCE ROW LEVEL SECURITY")
    op.execute(policy_create_statement("generated_reports"))
    for stmt in grant_app_role_statements():
        op.execute(stmt)


def downgrade() -> None:
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON generated_reports FROM {APP_ROLE}")
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON generated_reports")
    op.execute("ALTER TABLE generated_reports NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE generated_reports DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_generated_reports_tenant_created", table_name="generated_reports")
    op.drop_index("ix_generated_reports_tenant_id", table_name="generated_reports")
    op.drop_table("generated_reports")
