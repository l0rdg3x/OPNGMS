from sqlalchemy import ForeignKey, LargeBinary, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SyslogCaKey(Base):
    """Owner-only table holding the encrypted syslog CA private key (one row, id=1, FK→syslog_ca.id).

    Split out of `syslog_ca` in migration 0040 so the app role (`opngms_app`) cannot reach the key via
    the blanket SELECT grant. The app role reads it only through the SECURITY DEFINER function
    `opngms_syslog_ca_key()`; the owner (worker / bootstrap / rekey) reads it directly."""

    __tablename__ = "syslog_ca_key"

    id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("syslog_ca.id", ondelete="CASCADE"), primary_key=True,
        autoincrement=False)
    key_enc: Mapped[bytes] = mapped_column(LargeBinary)
