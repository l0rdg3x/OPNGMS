"""Use TEXT instead of VARCHAR for the hypertable string columns (Timescale best practice).

PostgreSQL treats `varchar` (unbounded) and `text` identically, but TimescaleDB emits a best-practice
WARNING for `character varying` columns on a hypertable. `varchar` -> `text` is a binary-coercible type
change, so this is a metadata-only ALTER (no table or chunk rewrite) and is safe even on the
composite-primary-key / indexed columns of the `metrics` and `events` hypertables.
"""

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

# (table, columns) that TimescaleDB warned about.
_METRICS = ("metric", "label")
_EVENTS = ("source", "event_key", "category", "src_ip", "dst_ip", "name", "severity", "action")


def upgrade() -> None:
    for col in _METRICS:
        op.execute(f"ALTER TABLE metrics ALTER COLUMN {col} TYPE text")
    for col in _EVENTS:
        op.execute(f"ALTER TABLE events ALTER COLUMN {col} TYPE text")


def downgrade() -> None:
    for col in _METRICS:
        op.execute(f"ALTER TABLE metrics ALTER COLUMN {col} TYPE varchar")
    for col in _EVENTS:
        op.execute(f"ALTER TABLE events ALTER COLUMN {col} TYPE varchar")
