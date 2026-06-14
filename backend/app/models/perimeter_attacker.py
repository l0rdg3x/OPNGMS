import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PerimeterAttacker(Base):
    """Bounded per-(device, kind, src_ip) rollup of perimeter threat observations.

    kind: 'login_failed' (failed box logins) | 'firewall_block' (blocked traffic). Tenant-scoped with a
    fail-closed RLS policy. The worker writes as owner; the API reads as opngms_app under the per-request
    tenant context. NOT per-packet — one row per distinct attacker IP per kind per device, so storage
    stays bounded regardless of traffic volume.
    """

    __tablename__ = "perimeter_attacker"
    __table_args__ = (
        # Backs the ranked "top attacker IPs per kind over a window" queries (must match migration 0034).
        Index(
            "ix_perimeter_attacker_rank",
            "tenant_id", "kind", "last_seen",
            postgresql_ops={"last_seen": "DESC"},
        ),
    )

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    src_ip: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    count: Mapped[int] = mapped_column(BigInteger, default=0)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
