"""events: composite DESC index to back the keyset-pagination ORDER BY"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Supports `ORDER BY time DESC, device_id DESC, source DESC, event_key DESC` with a leading
    # tenant_id filter (the GET /events keyset query). On a TimescaleDB hypertable the index is
    # propagated to all existing and future chunks automatically.
    op.create_index(
        "ix_events_keyset",
        "events",
        ["tenant_id", "time", "device_id", "source", "event_key"],
        postgresql_ops={
            "time": "DESC",
            "device_id": "DESC",
            "source": "DESC",
            "event_key": "DESC",
        },
    )


def downgrade() -> None:
    op.drop_index("ix_events_keyset", table_name="events")
