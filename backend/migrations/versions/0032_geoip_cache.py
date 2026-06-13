"""geoip_cache: cached GeoIP country mmdb (global, non-RLS) for attacker-country resolution"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.db_roles import grant_app_role_statements

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "geoip_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("sha256", sa.String(), nullable=False),
        sa.Column("mmdb", sa.LargeBinary(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", name="uq_geoip_cache_source"),
    )
    # Global table (no RLS) — provider/worker only. Reapply the blanket app-role grants so
    # opngms_app can read/write it (matches catalog_cache/smtp_settings/syslog_ca).
    for stmt in grant_app_role_statements():
        op.execute(stmt)


def downgrade() -> None:
    op.drop_table("geoip_cache")
