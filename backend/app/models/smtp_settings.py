from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, LargeBinary, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

SINGLETON_ID = 1


class SmtpSettings(Base):
    """Global (non-tenant) SMTP relay config — a single row (id=1). Password encrypted at rest.

    Not tenant-scoped: only the owner-connected worker and superadmin-gated API touch it, so no RLS.
    """

    __tablename__ = "smtp_settings"
    __table_args__ = (CheckConstraint("id = 1", name="ck_smtp_settings_singleton"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    enabled: Mapped[bool] = mapped_column(default=False, server_default="false")
    host: Mapped[str] = mapped_column(String, default="", server_default="")
    port: Mapped[int] = mapped_column(Integer, default=587, server_default="587")
    security: Mapped[str] = mapped_column(String, default="starttls", server_default="starttls")
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    password_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    from_email: Mapped[str] = mapped_column(String, default="", server_default="")
    from_name: Mapped[str] = mapped_column(String, default="", server_default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
