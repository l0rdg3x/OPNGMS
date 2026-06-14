"""Composite index on audit_log (action, ts) to support the superadmin Audit viewer: the common
query filters by action and orders by ts DESC, so this index serves both the filter and the sort."""
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_audit_log_action_ts", "audit_log", ["action", "ts"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_action_ts", table_name="audit_log")
