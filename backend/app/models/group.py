import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class Group(UUIDPKMixin, TimestampMixin, Base):
    """Org-level access group (e.g. 'MSP Staff'), superadmin-managed. NOT tenant-scoped (no RLS)."""
    __tablename__ = "groups"

    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String, default="", server_default="")


class GroupMember(UUIDPKMixin, Base):
    """Membership of a user in a group."""
    __tablename__ = "group_members"
    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_group_members_group_user"),
    )

    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )


class GroupGrant(UUIDPKMixin, Base):
    """A tenant-scoped role this group grants. Scope is the wildcard (all tenants) OR one tenant.

    A grant can NEVER carry an org/critical capability — `role` is one of the three tenant roles only.
    """
    __tablename__ = "group_grants"
    __table_args__ = (
        CheckConstraint(
            "(all_tenants AND tenant_id IS NULL) OR (NOT all_tenants AND tenant_id IS NOT NULL)",
            name="ck_group_grants_scope",
        ),
        # At most one wildcard grant per group, and at most one grant per (group, tenant).
        Index(
            "uq_group_grants_wildcard", "group_id", unique=True,
            postgresql_where=text("all_tenants"),
        ),
        Index(
            "uq_group_grants_tenant", "group_id", "tenant_id", unique=True,
            postgresql_where=text("tenant_id IS NOT NULL"),
        ),
    )

    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), index=True
    )
    all_tenants: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    role: Mapped[str] = mapped_column(String)  # tenant_admin | operator | read_only
