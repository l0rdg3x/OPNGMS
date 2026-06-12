"""device_log_forwarding.cert_not_after (cert expiry for the provisioning UX)"""
import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "device_log_forwarding",
        sa.Column("cert_not_after", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("device_log_forwarding", "cert_not_after")
