import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class ConfigProfile(UUIDPKMixin, Base):
    """Global MSP profile: a named, ordered bundle of templates. NOT tenant-scoped."""
    __tablename__ = "config_profiles"
    __table_args__ = (UniqueConstraint("name", name="uq_config_profiles_name"),)

    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String, default="", server_default="")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ConfigProfileMember(UUIDPKMixin, Base):
    """An ordered template membership of a profile. Global (MSP-defined)."""
    __tablename__ = "config_profile_members"
    __table_args__ = (
        UniqueConstraint("profile_id", "template_id", name="uq_profile_members_profile_template"),
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("config_profiles.id", ondelete="CASCADE"), index=True
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("config_templates.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
