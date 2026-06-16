"""trusted_device table (remember-this-device)

Revision ID: 0045
Revises: 0044
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trusted_devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_trusted_devices_user_id", "trusted_devices", ["user_id"])
    op.create_unique_constraint("uq_trusted_devices_token_hash", "trusted_devices", ["token_hash"])
    op.create_index("ix_trusted_devices_token_hash", "trusted_devices", ["token_hash"])
    op.create_index("ix_trusted_devices_expires_at", "trusted_devices", ["expires_at"])


def downgrade() -> None:
    op.drop_table("trusted_devices")
