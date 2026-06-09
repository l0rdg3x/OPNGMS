import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, text
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
    )

    # PK composita che include `time` (richiesto da Timescale) ed è anche la chiave
    # di deduplica: stesso (time, device, source, event_key) -> stesso evento.
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source: Mapped[str] = mapped_column(String, primary_key=True)         # 'ids' | 'dns'
    event_key: Mapped[str] = mapped_column(String, primary_key=True)      # id sorgente o hash contenuto
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    category: Mapped[str] = mapped_column(String, default="", server_default="")
    src_ip: Mapped[str] = mapped_column(String, default="", server_default="")
    dst_ip: Mapped[str] = mapped_column(String, default="", server_default="")
    name: Mapped[str] = mapped_column(String, default="", server_default="")
    severity: Mapped[str] = mapped_column(String, default="", server_default="")
    action: Mapped[str] = mapped_column(String, default="", server_default="")
    attributes: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
