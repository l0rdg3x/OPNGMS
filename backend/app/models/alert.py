import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class Alert(UUIDPKMixin, Base):
    __tablename__ = "alerts"
    # Un solo alert ATTIVO per (device, type, label) — indice unico parziale, dichiarato
    # sul modello per tenere alembic check pulito.
    __table_args__ = (
        Index(
            "uq_alerts_active",
            "device_id",
            "type",
            "label",
            unique=True,
            postgresql_where=text("resolved_at IS NULL"),
        ),
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
