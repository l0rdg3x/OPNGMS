import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class Session(UUIDPKMixin, Base):
    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Session scope: "full" (normal), "mfa_pending" (password ok, awaiting TOTP), or
    # "mfa_setup" (policy requires MFA but the user is not yet enrolled).
    kind: Mapped[str] = mapped_column(String(16), default="full", server_default="full")
    # SHA-256 hex of the opaque bearer token. The raw token lives only in the cookie;
    # a DB dump therefore yields no usable session tokens.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # Per-session CSRF secret, echoed by the SPA in the X-OPNGMS-CSRF header.
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Updated (throttled) on activity to drive the idle/sliding timeout.
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # Display-only metadata for the active-sessions list.
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
