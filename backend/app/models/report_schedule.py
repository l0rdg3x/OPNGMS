import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class ReportSchedule(UUIDPKMixin, Base):
    """A report delivery schedule. device_id NULL = tenant/fleet scope; set = that device."""

    __tablename__ = "report_schedule"
    __table_args__ = (
        CheckConstraint("hour BETWEEN 0 AND 23", name="ck_report_schedule_hour"),
        CheckConstraint("weekday IS NULL OR weekday BETWEEN 0 AND 6", name="ck_report_schedule_weekday"),
        Index(
            "uq_report_schedule_tenant", "tenant_id",
            unique=True, postgresql_where=text("device_id IS NULL"),
        ),
        Index(
            "uq_report_schedule_device", "tenant_id", "device_id",
            unique=True, postgresql_where=text("device_id IS NOT NULL"),
        ),
        Index("ix_report_schedule_due", "enabled", "next_run_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    frequency: Mapped[str] = mapped_column(String)  # weekly | monthly | on_demand
    weekday: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0=Mon..6=Sun (weekly)
    hour: Mapped[int] = mapped_column(Integer, default=4, server_default="4")
    recipients: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, server_default="{}")
    sections: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
