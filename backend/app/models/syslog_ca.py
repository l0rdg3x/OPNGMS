from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, SmallInteger, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

SINGLETON_ID = 1


class SyslogCa(Base):
    """Global (non-tenant) internal CA for the log pipeline — one row (id=1).

    Holds only the PUBLIC cert. The encrypted private key lives in the owner-only `syslog_ca_key`
    table (migration 0040): the app role can read this cert but not the key (least-privilege)."""

    __tablename__ = "syslog_ca"
    __table_args__ = (CheckConstraint("id = 1", name="ck_syslog_ca_singleton"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    cert_pem: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
