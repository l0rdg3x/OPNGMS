"""indexes (sessions/memberships/audit) + updated_at on the tables with TimestampMixin"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_TIMESTAMP_TABLES = ["tenants", "users", "memberships", "devices"]


def upgrade() -> None:
    for table in _TIMESTAMP_TABLES:
        op.add_column(
            table,
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])
    op.create_index("ix_memberships_tenant_id", "memberships", ["tenant_id"])
    op.create_index("ix_audit_log_tenant_id", "audit_log", ["tenant_id"])
    op.create_index("ix_audit_log_actor_user_id", "audit_log", ["actor_user_id"])
    op.create_index("ix_audit_log_ts", "audit_log", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_ts", "audit_log")
    op.drop_index("ix_audit_log_actor_user_id", "audit_log")
    op.drop_index("ix_audit_log_tenant_id", "audit_log")
    op.drop_index("ix_memberships_tenant_id", "memberships")
    op.drop_index("ix_sessions_expires_at", "sessions")
    op.drop_index("ix_sessions_user_id", "sessions")
    for table in reversed(_TIMESTAMP_TABLES):
        op.drop_column(table, "updated_at")
