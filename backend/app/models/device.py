import uuid
from datetime import datetime

from sqlalchemy import ARRAY, DateTime, ForeignKey, LargeBinary, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
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
    edition: Mapped[str] = mapped_column(default="", server_default="")
    firmware_series: Mapped[str] = mapped_column(default="", server_default="")
    # The source IP the box sees OPNGMS connecting from, auto-learned from the config-audit log
    # correlated with OPNGMS's own applied-change ledger. None until learned; drives api-change
    # attribution (opngms vs api_external drift). See app/services/ingest.py::_attribute_mgmt_ip.
    mgmt_source_ip: Mapped[str | None] = mapped_column(default=None)
    # Plugins the box reports (installed AND available-to-install), each {name, installed, version,
    # locked}; refreshed every poll, read by the Plugins UI to badge install state. [] until first poll.
    installed_plugins: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"))
