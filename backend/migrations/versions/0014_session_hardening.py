"""sessions: hardening columns (token_hash, csrf_token, last_seen_at, ip, user_agent)"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The bearer-token format changes from a raw UUID to a hashed opaque token, so
    # existing cookies can no longer be matched. Clearing the table lets the new
    # NOT NULL columns be added cleanly and forces a one-time re-login.
    op.execute("DELETE FROM sessions")
    op.add_column("sessions", sa.Column("token_hash", sa.String(64), nullable=False))
    op.add_column("sessions", sa.Column("csrf_token", sa.String(64), nullable=False))
    op.add_column(
        "sessions",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column("sessions", sa.Column("ip", sa.String(45), nullable=True))
    op.add_column("sessions", sa.Column("user_agent", sa.String(512), nullable=True))
    op.create_index("ix_sessions_token_hash", "sessions", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_sessions_token_hash", table_name="sessions")
    op.drop_column("sessions", "user_agent")
    op.drop_column("sessions", "ip")
    op.drop_column("sessions", "last_seen_at")
    op.drop_column("sessions", "csrf_token")
    op.drop_column("sessions", "token_hash")
