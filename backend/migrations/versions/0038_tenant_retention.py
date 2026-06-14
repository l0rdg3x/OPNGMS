"""tenant_retention (per-tenant retention overrides, RLS)"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.db_roles import APP_ROLE, grant_app_role_statements
from app.core.rls import POLICY_NAME, policy_create_statement

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_retention",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("overrides", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id"),
    )
    op.execute("ALTER TABLE tenant_retention ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_retention FORCE ROW LEVEL SECURITY")
    op.execute(policy_create_statement("tenant_retention"))
    for stmt in grant_app_role_statements():
        op.execute(stmt)


def downgrade() -> None:
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON tenant_retention FROM {APP_ROLE}")
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON tenant_retention")
    op.execute("ALTER TABLE tenant_retention NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_retention DISABLE ROW LEVEL SECURITY")
    op.drop_table("tenant_retention")
