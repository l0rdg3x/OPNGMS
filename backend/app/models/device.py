import uuid
from datetime import datetime

from sqlalchemy import ARRAY, DateTime, ForeignKey, LargeBinary, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class Device(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "devices"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str]
    base_url: Mapped[str]
    api_key_enc: Mapped[bytes] = mapped_column(LargeBinary)
    api_secret_enc: Mapped[bytes] = mapped_column(LargeBinary)
    verify_tls: Mapped[bool] = mapped_column(default=True)
    tls_fingerprint: Mapped[str | None] = mapped_column(default=None)
    site: Mapped[str | None] = mapped_column(default=None)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    status: Mapped[str] = mapped_column(default="unverified")  # reachable|unverified|unreachable
    last_seen: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    firmware_version: Mapped[str | None] = mapped_column(default=None)
