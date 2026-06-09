"""TimescaleDB: extension + metrics hypertable + retention"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    op.create_table(
        "metrics",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False, server_default=""),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("time", "device_id", "metric", "label"),
    )
    op.execute("SELECT create_hypertable('metrics', 'time')")
    op.create_index(
        "ix_metrics_tenant_device_metric_time",
        "metrics",
        ["tenant_id", "device_id", "metric", "time"],
    )
    op.execute("SELECT add_retention_policy('metrics', INTERVAL '30 days')")


def downgrade() -> None:
    op.execute("SELECT remove_retention_policy('metrics', if_exists => true)")
    op.drop_table("metrics")
