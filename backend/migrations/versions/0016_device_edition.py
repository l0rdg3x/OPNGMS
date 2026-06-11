"""device edition + firmware_series

Revision ID: 0016
Revises: 0015
"""
import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("edition", sa.String(), nullable=False, server_default=""))
    op.add_column("devices", sa.Column("firmware_series", sa.String(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("devices", "firmware_series")
    op.drop_column("devices", "edition")
