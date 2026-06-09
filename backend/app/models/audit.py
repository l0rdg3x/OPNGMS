import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class AuditLog(UUIDPKMixin, Base):
    __tablename__ = "audit_log"

    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), default=None, index=True
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), default=None, index=True
    )
    action: Mapped[str]
    target_type: Mapped[str | None] = mapped_column(default=None)
    target_id: Mapped[str | None] = mapped_column(default=None)
    ip: Mapped[str | None] = mapped_column(default=None)
    details: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
