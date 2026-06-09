import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Metric(Base):
    __tablename__ = "metrics"
    # Indice dichiarato sul modello -> create_all (test) lo crea e alembic non vede drift.
    __table_args__ = (
        Index("ix_metrics_tenant_device_metric_time", "tenant_id", "device_id", "metric", "time"),
    )

    # PK composita che INCLUDE la colonna di partizionamento `time` (richiesto da Timescale).
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    metric: Mapped[str] = mapped_column(String, primary_key=True)
    label: Mapped[str] = mapped_column(String, primary_key=True, default="")
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    value: Mapped[float] = mapped_column(Float)
