import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class SilentTenantAlert(UUIDPKMixin, Base):
    """Global (non-tenant) MSP-level alert state for a tenant gone silent (enabled forwarding but no
    recent logs). One row per silent tenant: created on entering the silent state (emailed once),
    deleted on recovery. Backs the dashboard banner + the email dedup. Not RLS — superadmin/worker
    only."""

    __tablename__ = "silent_tenant_alerts"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), unique=True, index=True)
    tenant_name: Mapped[str] = mapped_column(String)
    silent_since: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_alert_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    details: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
