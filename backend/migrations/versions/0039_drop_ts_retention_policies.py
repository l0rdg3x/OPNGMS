"""Drop native TimescaleDB retention policies on events/metrics (per-tenant purge takes over).

Forward-only in practice. The replacement is the worker's daily ``purge_timeseries_retention`` cron
(per-tenant cutoffs via ``tenant_retention``), which lands in the SAME PR — so there is never an
unbounded-growth window between dropping the global policy and the per-tenant purge taking over.

The downgrade re-adds the old global policies (events = 90 days, metrics = 30 days), mirroring the
``add_retention_policy`` calls in migrations 0008/0005, in case a manual rollback is ever needed.
"""
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # if_exists => true keeps this idempotent (e.g. a fresh DB built from metadata never had a policy).
    op.execute("SELECT remove_retention_policy('events', if_exists => true)")
    op.execute("SELECT remove_retention_policy('metrics', if_exists => true)")


def downgrade() -> None:
    # Restore the original global policies (see migrations 0008 / 0005).
    op.execute("SELECT add_retention_policy('events', INTERVAL '90 days')")
    op.execute("SELECT add_retention_policy('metrics', INTERVAL '30 days')")
