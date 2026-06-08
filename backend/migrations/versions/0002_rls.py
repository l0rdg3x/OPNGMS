"""row-level security policies on tenant-scoped tables"""

from alembic import op

from app.core.rls import disable_rls_statements, enable_rls_statements

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for stmt in enable_rls_statements():
        op.execute(stmt)


def downgrade() -> None:
    for stmt in disable_rls_statements():
        op.execute(stmt)
