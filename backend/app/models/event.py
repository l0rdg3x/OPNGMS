import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index(
            "ix_events_tenant_device_source_time",
            "tenant_id", "device_id", "source", "time",
        ),
        # Backs the keyset-pagination ORDER BY (time, device_id, source, event_key) DESC under a
        # tenant filter. Mirrors migration 0015 (name + columns + DESC ops must match).
        Index(
            "ix_events_keyset",
            "tenant_id", "time", "device_id", "source", "event_key",
            postgresql_ops={"time": "DESC", "device_id": "DESC", "source": "DESC", "event_key": "DESC"},
        ),
    )

    # Composite PK that includes `time` (required by Timescale) and is also the
    # dedup key: same (time, device, source, event_key) -> same event.
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source: Mapped[str] = mapped_column(Text, primary_key=True)         # 'ids' | 'dns'
    event_key: Mapped[str] = mapped_column(Text, primary_key=True)      # source id or content hash
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    category: Mapped[str] = mapped_column(Text, default="", server_default="")
    src_ip: Mapped[str] = mapped_column(Text, default="", server_default="")
    dst_ip: Mapped[str] = mapped_column(Text, default="", server_default="")
    name: Mapped[str] = mapped_column(Text, default="", server_default="")
    severity: Mapped[str] = mapped_column(Text, default="", server_default="")
    action: Mapped[str] = mapped_column(Text, default="", server_default="")
    attributes: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
