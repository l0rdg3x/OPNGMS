"""split syslog CA private key into an owner-only table + SECURITY DEFINER accessor

Least-privilege: move `syslog_ca.key_enc` (the encrypted CA private key) out of `syslog_ca` into a new
owner-only table `syslog_ca_key`. The app role keeps SELECT on `syslog_ca` (the public cert is fine to
read) but loses the blanket grant on the key; it may read the key only through the single-purpose
SECURITY DEFINER function `opngms_syslog_ca_key()`.
"""
import sqlalchemy as sa
from alembic import op

from app.core.db_roles import syslog_ca_key_least_priv_statements

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "syslog_ca_key",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("key_enc", sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(["id"], ["syslog_ca.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Move the key out of syslog_ca (no-op on a fresh DB with no CA row yet).
    op.execute("INSERT INTO syslog_ca_key (id, key_enc) SELECT id, key_enc FROM syslog_ca WHERE key_enc IS NOT NULL")
    op.drop_column("syslog_ca", "key_enc")
    # REVOKE the just-granted blanket privilege + create the owner-only accessor function.
    for stmt in syslog_ca_key_least_priv_statements():
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS opngms_syslog_ca_key()")
    op.add_column("syslog_ca", sa.Column("key_enc", sa.LargeBinary(), nullable=True))
    op.execute("UPDATE syslog_ca s SET key_enc = k.key_enc FROM syslog_ca_key k WHERE k.id = s.id")
    op.drop_table("syslog_ca_key")
