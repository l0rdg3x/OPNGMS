import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class IngestCursor(Base):
    """Watermark per-(device, source) dell'ingest. Stato interno del worker, NON user-facing
    (niente RLS): mai esposto via API."""

    __tablename__ = "ingest_cursors"

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(String, primary_key=True)
    last_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_ref: Mapped[str | None] = mapped_column(String, default=None)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
