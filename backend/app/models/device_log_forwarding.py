import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DeviceLogForwarding(Base):
    """Per-device log-forwarding provisioning state (tenant-scoped, RLS)."""

    __tablename__ = "device_log_forwarding"

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    cert_serial: Mapped[str] = mapped_column(String, default="", server_default="")
    cert_fingerprint: Mapped[str] = mapped_column(String, default="", server_default="")
    opnsense_ca_uuid: Mapped[str | None] = mapped_column(String, nullable=True)
    opnsense_cert_uuid: Mapped[str | None] = mapped_column(String, nullable=True)
    opnsense_dest_uuid: Mapped[str | None] = mapped_column(String, nullable=True)
    provisioned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cert_not_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
