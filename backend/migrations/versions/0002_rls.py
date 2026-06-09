"""row-level security policies on tenant-scoped tables"""

from alembic import op

from app.core.rls import disable_rls_statements, enable_rls_statements

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# At this point in history only `devices` exists; metrics/alerts are created
# later (0005/0006) and RLS-enabled by 0007. We pin the subset here so that
# the migration stays stable even when TENANT_TABLES grows.
_TABLES = ["devices"]


def upgrade() -> None:
    for stmt in enable_rls_statements(_TABLES):
        op.execute(stmt)


def downgrade() -> None:
    for stmt in disable_rls_statements(_TABLES):
        op.execute(stmt)
