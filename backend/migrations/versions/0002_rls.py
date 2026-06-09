"""row-level security policies on tenant-scoped tables"""

from alembic import op

from app.core.rls import disable_rls_statements, enable_rls_statements

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# A questo punto della cronologia esiste solo `devices`; metrics/alerts vengono
# create dopo (0005/0006) e abilitate alla RLS dalla 0007. Fissiamo qui il
# sottoinsieme cosi' che la migrazione resti stabile anche quando TENANT_TABLES
# cresce.
_TABLES = ["devices"]


def upgrade() -> None:
    for stmt in enable_rls_statements(_TABLES):
        op.execute(stmt)


def downgrade() -> None:
    for stmt in disable_rls_statements(_TABLES):
        op.execute(stmt)
