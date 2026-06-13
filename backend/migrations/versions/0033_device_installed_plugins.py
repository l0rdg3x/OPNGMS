"""device_installed_plugins: per-device plugin install-state telemetry (JSONB), refreshed on poll"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column(
            "installed_plugins",
            postgresql.JSONB(),
            nullable=False,
            server_default=text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("devices", "installed_plugins")
