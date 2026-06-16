"""webauthn_credential table + sessions.webauthn_challenge"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webauthn_credential",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("credential_id", sa.LargeBinary(), nullable=False, unique=True),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("transports", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("aaguid", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("sessions", sa.Column("webauthn_challenge", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "webauthn_challenge")
    op.drop_table("webauthn_credential")
