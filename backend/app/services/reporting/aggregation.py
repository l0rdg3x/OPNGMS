"""Tenant-scoped report aggregations over the events/metrics hypertables (RLS + tenant filter)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.event import EventRepository
from app.schemas.event import EventTopRow

# Allowlist of TimescaleDB time_bucket widths. `bucket` is interpolated into the SQL only after
# being checked against this set (asyncpg cannot bind a Python str as a PG interval), so the
# allowlist — not parameter binding — is what makes the interpolation injection-safe.
_BUCKETS = ("1 hour", "6 hours", "1 day")


def pick_bucket(span: timedelta) -> str:
    if span <= timedelta(days=2):
        return "1 hour"
    if span <= timedelta(days=14):
        return "6 hours"
    return "1 day"


@dataclass
class DeviceRow:
    id: uuid.UUID
    name: str


class ReportAggregator:
    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id
        self._events = EventRepository(session, tenant_id)

    async def devices(self) -> list[DeviceRow]:
        rows = (
            await self.session.execute(
                text("SELECT id, name FROM devices WHERE tenant_id = :tid ORDER BY name"),
                {"tid": self.tenant_id},
            )
        ).all()
        return [DeviceRow(id=r.id, name=r.name) for r in rows]

    async def top(
        self, *, field: str, frm: datetime, to: datetime, source: str = "ids", limit: int = 10
    ) -> list[EventTopRow]:
        return await self._events.top(field=field, source=source, frm=frm, to=to, limit=limit)

    async def timeline(
        self, *, frm: datetime, to: datetime, bucket: str, source: str = "ids"
    ) -> list[tuple[datetime, int]]:
        if bucket not in _BUCKETS:
            raise ValueError(f"bucket not allowed: {bucket}")
        # `bucket` is validated against the _BUCKETS allowlist above, so it is safe to
        # interpolate directly into SQL (no user-controlled input reaches here).
        # asyncpg cannot bind a plain Python str as a PostgreSQL `interval` parameter,
        # so we use a literal interval string instead of a bound placeholder.
        sql = text(
            f"SELECT time_bucket('{bucket}'::interval, time) AS b, count(*) AS c "
            "FROM events WHERE tenant_id = :tid AND source = :source "
            "AND time >= :frm AND time < :to GROUP BY b ORDER BY b"
        )
        rows = (
            await self.session.execute(
                sql,
                {"tid": self.tenant_id, "source": source, "frm": frm, "to": to},
            )
        ).all()
        return [(r.b, int(r.c)) for r in rows]
