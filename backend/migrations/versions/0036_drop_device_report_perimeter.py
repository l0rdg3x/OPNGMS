"""Drop devices.report_perimeter: the perimeter report sections are now toggled like every other
report section (report_settings.sections / report_schedule.sections), not per-device on the box."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None

_DEFAULT = '{"failed_logins": true, "firewall_blocks": true}'


def upgrade() -> None:
    op.drop_column("devices", "report_perimeter")


def downgrade() -> None:
    op.add_column(
        "devices",
        sa.Column(
            "report_perimeter",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text(f"'{_DEFAULT}'::jsonb"),
        ),
    )
