"""perimeter_attacker (tenant-scoped rollup, RLS): failed logins + firewall blocks per attacker IP"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.db_roles import APP_ROLE, grant_app_role_statements
from app.core.rls import POLICY_NAME, policy_create_statement

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "perimeter_attacker",
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("src_ip", sa.Text(), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("device_id", "kind", "src_ip"),
    )
    # Backs the ranked "top attacker IPs per kind over a window" queries (Overview cards + page + report).
    op.create_index(
        "ix_perimeter_attacker_rank",
        "perimeter_attacker",
        ["tenant_id", "kind", sa.text("last_seen DESC")],
    )
    op.execute("ALTER TABLE perimeter_attacker ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE perimeter_attacker FORCE ROW LEVEL SECURITY")
    op.execute(policy_create_statement("perimeter_attacker"))
    for stmt in grant_app_role_statements():
        op.execute(stmt)


def downgrade() -> None:
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON perimeter_attacker FROM {APP_ROLE}")
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON perimeter_attacker")
    op.execute("ALTER TABLE perimeter_attacker NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE perimeter_attacker DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_perimeter_attacker_rank", table_name="perimeter_attacker")
    op.drop_table("perimeter_attacker")
