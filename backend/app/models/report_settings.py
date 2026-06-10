import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ReportSettings(Base):
    __tablename__ = "report_settings"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    title: Mapped[str] = mapped_column(String, default="Security & Activity Report",
                                       server_default="Security & Activity Report")
    owner: Mapped[str] = mapped_column(String, default="", server_default="")
    timezone: Mapped[str] = mapped_column(String, default="UTC", server_default="UTC")
    logo: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    logo_mime: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
