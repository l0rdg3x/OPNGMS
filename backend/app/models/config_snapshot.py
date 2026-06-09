import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class ConfigSnapshot(UUIDPKMixin, Base):
    __tablename__ = "config_snapshots"
    __table_args__ = (
        Index("ix_config_snapshots_tenant_device_taken", "tenant_id", "device_id", "taken_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    canonical_hash: Mapped[str] = mapped_column(String)
    content_enc: Mapped[bytes] = mapped_column(LargeBinary)  # Fernet(gzip(config.xml))
    opnsense_version: Mapped[str] = mapped_column(String, default="", server_default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
