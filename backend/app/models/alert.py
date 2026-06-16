import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class Alert(UUIDPKMixin, Base):
    __tablename__ = "alerts"
    # Only one ACTIVE alert per (device, type, label) — partial unique index, declared
    # on the model to keep the alembic check clean.
    __table_args__ = (
        Index(
            "uq_alerts_active",
            "device_id",
            "type",
            "label",
            unique=True,
            postgresql_where=text("resolved_at IS NULL"),
        ),
        # Backs the per-report, per-device `alerts_in_range` query (WHERE tenant_id + device_id +
        # opened_at range, ORDER BY opened_at DESC) — a btree range-scan instead of scanning all of a
        # device's history + sorting. Mirrors the (tenant_id, device_id, <time>) pattern used by
        # config_changes / config_snapshots / firmware_actions.
        Index("ix_alerts_tenant_device_opened", "tenant_id", "device_id", "opened_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String)
    label: Mapped[str] = mapped_column(String, default="")
    severity: Mapped[str] = mapped_column(String, default="warning")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    details: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
