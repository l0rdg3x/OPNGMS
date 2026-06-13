from datetime import datetime

from sqlalchemy import DateTime, LargeBinary, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class GeoipCache(UUIDPKMixin, Base):
    """Cached GeoIP country database (mmdb bytes) fetched from the `geoip` release.

    Global, non-tenant: only the provider/worker path touches it, so no RLS — the blanket app-role
    grants (like catalog_cache/smtp_settings/syslog_ca) let opngms_app read/write it. Keyed by `source`
    (e.g. "dbip-country"); the bytes are SHA-256-verified against the release manifest before caching.
    """

    __tablename__ = "geoip_cache"
    __table_args__ = (UniqueConstraint("source", name="uq_geoip_cache_source"),)

    source: Mapped[str] = mapped_column(String, nullable=False)
    sha256: Mapped[str] = mapped_column(String, nullable=False)
    mmdb: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
