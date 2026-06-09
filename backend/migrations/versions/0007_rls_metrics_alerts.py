"""RLS on metrics + alerts; grant to opngms_app (with propagation to the Timescale chunks)"""

from alembic import op

from app.core.db_roles import APP_ROLE, grant_app_role_statements
from app.core.rls import POLICY_NAME, policy_create_statement

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_NEW_TABLES = ["metrics", "alerts"]


def upgrade() -> None:
    for table in _NEW_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(policy_create_statement(table))
    # The metrics/alerts tables were created AFTER the GRANT ON ALL TABLES of 0003:
    # we re-run the grants now that they exist. On `metrics` (hypertable) the explicit GRANT
    # propagates the privilege to the TimescaleDB chunks (existing and future).
    for stmt in grant_app_role_statements():
        op.execute(stmt)
    # Explicit GRANT only on `metrics`: needed to propagate the privilege to the
    # TimescaleDB hypertable chunks. `alerts` has NO explicit grant because it is not
    # a hypertable: it is already covered by the GRANT ON ALL TABLES above.
    op.execute(f"GRANT SELECT ON metrics TO {APP_ROLE}")


def downgrade() -> None:
    # Symmetric revoke with respect to the upgrade: the upgrade granted DML on all
    # tables (including metrics/alerts) via GRANT ON ALL TABLES + explicit SELECT on
    # metrics. Here we revoke everything before disabling RLS.
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON metrics, alerts FROM {APP_ROLE}")
    for table in _NEW_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
