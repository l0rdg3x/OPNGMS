"""app role non-superuser + refresh policy tenant_isolation (NULLIF)"""

from alembic import op

from app.core.db_roles import (
    create_app_role_statements,
    drop_app_role_statements,
    grant_app_role_statements,
)
from app.core.rls import recreate_policy_statements

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

# Solo `devices` esiste a questo punto: metrics/alerts arrivano dopo (0005/0006).
_TABLES = ["devices"]


def upgrade() -> None:
    for stmt in create_app_role_statements():
        op.execute(stmt)
    for stmt in recreate_policy_statements(_TABLES):
        op.execute(stmt)
    for stmt in grant_app_role_statements():
        op.execute(stmt)


def downgrade() -> None:
    for stmt in drop_app_role_statements():
        op.execute(stmt)
