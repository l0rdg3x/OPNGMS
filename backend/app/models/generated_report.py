import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class GeneratedReport(UUIDPKMixin, Base):
    __tablename__ = "generated_reports"
    __table_args__ = (
        Index("ix_generated_reports_tenant_created", "tenant_id", "created_at"),
    )

    # No standalone index: the composite ix_generated_reports_tenant_created already covers
    # tenant_id as a leading-column prefix (and serves the FK).
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String)                 # 'on_demand' | 'scheduled'
    period_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_to: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    pdf: Mapped[bytes] = mapped_column(LargeBinary)
    size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
