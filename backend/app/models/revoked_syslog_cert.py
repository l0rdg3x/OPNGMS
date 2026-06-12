import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RevokedSyslogCert(Base):
    """Ledger of revoked per-device log-forwarding client certs (tenant-scoped, RLS).

    The CRL input for Phase 3.2-bis: each row records a revoked cert's serial so a future
    CRL can reject it at the syslog-ng receiver."""

    __tablename__ = "revoked_syslog_certs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE")
    )
    serial: Mapped[str] = mapped_column(String(64))  # hex cert serial (<= 20 octets -> 40 hex chars)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
