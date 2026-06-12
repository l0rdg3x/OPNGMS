from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class CatalogCache(UUIDPKMixin, Base):
    """Cached versioned OPNsense catalog (JSON) fetched from the `catalogs` release.

    Global, non-tenant: only the provider/worker/superadmin path touches it, so no RLS — the blanket
    app-role grants (like smtp_settings/syslog_ca) let opngms_app read/write it. Keyed by the RESOLVED
    identity (edition, version); a Business device reuses its Community base row.
    """

    __tablename__ = "catalog_cache"
    __table_args__ = (UniqueConstraint("edition", "version", name="uq_catalog_cache_edition_version"),)

    edition: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)
    sha256: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
