import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class TrustedDevice(UUIDPKMixin, Base):
    """A per-(user, device) trust grant: presenting the matching cookie at login lets the user skip
    the second factor (the password is still required). Mirrors the session-token model — the raw
    token lives only in the cookie; only its HMAC-SHA256(SESSION_SECRET) hash is stored here."""

    __tablename__ = "trusted_devices"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # HMAC-SHA256 hex of the opaque device token. A DB dump yields no usable tokens, and rotating
    # SESSION_SECRET invalidates every trusted device — same property as sessions.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # Display-only metadata for the "trusted devices" list (never enforced).
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
