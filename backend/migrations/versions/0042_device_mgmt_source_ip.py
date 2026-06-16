"""device.mgmt_source_ip — auto-learned management IP for config-audit attribution"""

import sqlalchemy as sa
from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("mgmt_source_ip", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("devices", "mgmt_source_ip")
