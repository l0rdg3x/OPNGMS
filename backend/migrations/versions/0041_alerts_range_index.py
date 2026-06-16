"""alerts (tenant_id, device_id, opened_at) index for alerts_in_range (perf+refactor 2/4 · PR2)

Backend-perf index audit: the only hot read path lacking a supporting index was the reporting
`alerts_in_range` query (WHERE tenant_id + device_id + opened_at range, ORDER BY opened_at DESC), run
once per device per report. Adds the composite index; mirrors the existing tenant-device-time pattern on
config_changes / config_snapshots / firmware_actions. Additive only — no data change, no query rewrite.
"""

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_alerts_tenant_device_opened",
        "alerts",
        ["tenant_id", "device_id", "opened_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_alerts_tenant_device_opened", table_name="alerts")
