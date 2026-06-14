"""devices.report_perimeter: per-device toggles for the two perimeter report sections (default on)"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

_DEFAULT = '{"failed_logins": true, "firewall_blocks": true}'


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column(
            "report_perimeter",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text(f"'{_DEFAULT}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("devices", "report_perimeter")
