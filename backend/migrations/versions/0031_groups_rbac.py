"""groups RBAC: groups, group_members, group_grants (org-level, non-RLS) for group-based access"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.db_roles import grant_app_role_statements

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "group_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_id", "user_id", name="uq_group_members_group_user"),
    )
    op.create_index("ix_group_members_group_id", "group_members", ["group_id"])
    op.create_index("ix_group_members_user_id", "group_members", ["user_id"])
    op.create_table(
        "group_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("all_tenants", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("role", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "(all_tenants AND tenant_id IS NULL) OR (NOT all_tenants AND tenant_id IS NOT NULL)",
            name="ck_group_grants_scope",
        ),
    )
    op.create_index("ix_group_grants_group_id", "group_grants", ["group_id"])
    op.create_index("ix_group_grants_tenant_id", "group_grants", ["tenant_id"])
    # At most one wildcard grant per group; at most one per (group, tenant).
    op.create_index(
        "uq_group_grants_wildcard", "group_grants", ["group_id"], unique=True,
        postgresql_where=sa.text("all_tenants"),
    )
    op.create_index(
        "uq_group_grants_tenant", "group_grants", ["group_id", "tenant_id"], unique=True,
        postgresql_where=sa.text("tenant_id IS NOT NULL"),
    )
    # Org-level tables (no RLS): reapply the blanket app-role grants so opngms_app can read/write them.
    for stmt in grant_app_role_statements():
        op.execute(stmt)


def downgrade() -> None:
    op.drop_table("group_grants")
    op.drop_table("group_members")
    op.drop_table("groups")
