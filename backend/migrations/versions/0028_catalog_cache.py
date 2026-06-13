"""catalog_cache: cached versioned OPNsense catalogs (global, non-RLS) for the generic editor"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.db_roles import grant_app_role_statements

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "catalog_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("edition", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("sha256", sa.String(), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("edition", "version", name="uq_catalog_cache_edition_version"),
    )
    # Global table (no RLS) — provider/worker/superadmin only. Reapply the blanket app-role grants
    # so opngms_app can read/write it (matches smtp_settings/syslog_ca/silent_tenant_alerts).
    for stmt in grant_app_role_statements():
        op.execute(stmt)


def downgrade() -> None:
    op.drop_table("catalog_cache")
