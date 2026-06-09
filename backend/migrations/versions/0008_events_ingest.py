"""events hypertable + ingest_cursors + RLS on events"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.db_roles import APP_ROLE, grant_app_role_statements
from app.core.rls import POLICY_NAME, policy_create_statement

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # events (hypertable)
    op.create_table(
        "events",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("event_key", sa.String(), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(), nullable=False, server_default=""),
        sa.Column("src_ip", sa.String(), nullable=False, server_default=""),
        sa.Column("dst_ip", sa.String(), nullable=False, server_default=""),
        sa.Column("name", sa.String(), nullable=False, server_default=""),
        sa.Column("severity", sa.String(), nullable=False, server_default=""),
        sa.Column("action", sa.String(), nullable=False, server_default=""),
        sa.Column("attributes", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.PrimaryKeyConstraint("time", "device_id", "source", "event_key"),
    )
    op.execute("SELECT create_hypertable('events', 'time')")
    op.create_index(
        "ix_events_tenant_device_source_time",
        "events",
        ["tenant_id", "device_id", "source", "time"],
    )
    op.execute("SELECT add_retention_policy('events', INTERVAL '90 days')")

    # ingest_cursors (worker state, no RLS)
    op.create_table(
        "ingest_cursors",
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("last_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ref", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("device_id", "source"),
    )

    # RLS on events + grant to opngms_app (with propagation to the Timescale chunks)
    op.execute("ALTER TABLE events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE events FORCE ROW LEVEL SECURITY")
    op.execute(policy_create_statement("events"))
    for stmt in grant_app_role_statements():
        op.execute(stmt)
    op.execute(f"GRANT SELECT ON events TO {APP_ROLE}")  # propagates to the hypertable chunks
    # ingest_cursors is not user-facing: no RLS.


def downgrade() -> None:
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON events FROM {APP_ROLE}")
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON events")
    op.execute("ALTER TABLE events NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE events DISABLE ROW LEVEL SECURITY")
    op.drop_table("ingest_cursors")
    op.execute("SELECT remove_retention_policy('events', if_exists => true)")
    op.drop_table("events")
