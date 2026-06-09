import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class IngestCursor(Base):
    """Per-(device, source) ingest watermark. Internal worker state, NOT user-facing
    (no RLS): never exposed via the API."""

    __tablename__ = "ingest_cursors"

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(String, primary_key=True)
    last_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_ref: Mapped[str | None] = mapped_column(String, default=None)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
