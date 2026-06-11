"""config_profiles + config_profile_members (global) + config_changes.source_profile_id"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.db_roles import grant_app_role_statements

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "config_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_config_profiles_name"),
    )
    op.create_table(
        "config_profile_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["profile_id"], ["config_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["template_id"], ["config_templates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_id", "template_id", name="uq_profile_members_profile_template"),
    )
    op.create_index("ix_config_profile_members_profile_id", "config_profile_members", ["profile_id"])
    op.create_index("ix_config_profile_members_template_id", "config_profile_members", ["template_id"])
    op.add_column(
        "config_changes",
        sa.Column("source_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_config_changes_source_profile", "config_changes", "config_profiles",
        ["source_profile_id"], ["id"], ondelete="SET NULL",
    )
    for stmt in grant_app_role_statements():
        op.execute(stmt)


def downgrade() -> None:
    op.drop_constraint("fk_config_changes_source_profile", "config_changes", type_="foreignkey")
    op.drop_column("config_changes", "source_profile_id")
    op.drop_table("config_profile_members")
    op.drop_table("config_profiles")
