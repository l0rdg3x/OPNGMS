"""RLS su metrics + alerts; grant a opngms_app (con propagazione ai chunk Timescale)"""

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
    # Le tabelle metrics/alerts sono state create DOPO il GRANT ON ALL TABLES della 0003:
    # ri-eseguiamo i grant ora che esistono. Su `metrics` (hypertable) il GRANT esplicito
    # fa propagare il privilegio ai chunk TimescaleDB (esistenti e futuri).
    for stmt in grant_app_role_statements():
        op.execute(stmt)
    op.execute(f"GRANT SELECT ON metrics TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE SELECT ON metrics FROM {APP_ROLE}")
    for table in _NEW_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
