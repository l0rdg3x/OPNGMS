import uuid

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class UserRecoveryCode(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "user_recovery_code"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    code_hash: Mapped[str] = mapped_column(String)
    used_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=None, nullable=True)
