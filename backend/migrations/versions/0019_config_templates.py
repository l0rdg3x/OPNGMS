"""config_templates (global) + template_overrides (RLS) + config_changes.source_template_id"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.db_roles import APP_ROLE, grant_app_role_statements
from app.core.rls import POLICY_NAME, policy_create_statement

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- global library: NO RLS policy (superadmin-gated at the API layer) ---
    op.create_table(
        "config_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False, server_default=""),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "name", name="uq_config_templates_kind_name"),
    )

    # --- per-tenant override: tenant-scoped RLS ---
    op.create_table(
        "template_overrides",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("body_patch", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["config_templates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("template_id", "tenant_id", name="uq_template_overrides_template_tenant"),
    )
    op.create_index("ix_template_overrides_template_id", "template_overrides", ["template_id"])
    op.create_index("ix_template_overrides_tenant_id", "template_overrides", ["tenant_id"])
    op.execute("ALTER TABLE template_overrides ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE template_overrides FORCE ROW LEVEL SECURITY")
    op.execute(policy_create_statement("template_overrides"))

    # --- tag config_changes with its source template (nullable; history-preserving) ---
    op.add_column(
        "config_changes",
        sa.Column("source_template_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_config_changes_source_template", "config_changes", "config_templates",
        ["source_template_id"], ["id"], ondelete="SET NULL",
    )

    # grants on ALL tables incl. the two new ones (same as 0018)
    for stmt in grant_app_role_statements():
        op.execute(stmt)


def downgrade() -> None:
    op.drop_constraint("fk_config_changes_source_template", "config_changes", type_="foreignkey")
    op.drop_column("config_changes", "source_template_id")
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON template_overrides")
    op.execute("ALTER TABLE template_overrides NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE template_overrides DISABLE ROW LEVEL SECURITY")
    op.drop_table("template_overrides")
    op.drop_table("config_templates")
