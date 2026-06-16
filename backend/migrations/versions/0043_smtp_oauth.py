"""smtp_settings OAuth columns"""

import sqlalchemy as sa
from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("smtp_settings", sa.Column(
        "auth_method", sa.String(), nullable=False, server_default="password"))
    op.add_column("smtp_settings", sa.Column("oauth_provider", sa.String(), nullable=True))
    op.add_column("smtp_settings", sa.Column("oauth_client_id", sa.String(), nullable=True))
    op.add_column("smtp_settings", sa.Column("oauth_client_secret_enc", sa.LargeBinary(), nullable=True))
    op.add_column("smtp_settings", sa.Column("oauth_refresh_token_enc", sa.LargeBinary(), nullable=True))
    op.add_column("smtp_settings", sa.Column("oauth_tenant_id", sa.String(), nullable=True))


def downgrade() -> None:
    for c in ("oauth_tenant_id", "oauth_refresh_token_enc", "oauth_client_secret_enc",
              "oauth_client_id", "oauth_provider", "auth_method"):
        op.drop_column("smtp_settings", c)
