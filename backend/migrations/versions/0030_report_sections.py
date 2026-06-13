"""report sections toggles: JSONB toggle maps on report_settings + report_schedule.

Foundation of the report-enrichment feature: a single JSONB source of truth for which
report sections render. ``report_settings.sections`` is the tenant-level default map
(NOT NULL, defaults to ``{}``); ``report_schedule.sections`` is an optional per-schedule
(so per-device) override (nullable -> inherit the tenant default).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "report_settings",
        sa.Column("sections", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
    )
    op.add_column(
        "report_schedule",
        sa.Column("sections", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("report_schedule", "sections")
    op.drop_column("report_settings", "sections")
