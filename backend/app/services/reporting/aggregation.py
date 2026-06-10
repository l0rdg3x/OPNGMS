"""Tenant-scoped report aggregations over the events/metrics hypertables (RLS + tenant filter)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.event import TOP_FIELDS
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

    async def devices(self) -> list[DeviceRow]:
        rows = (
            await self.session.execute(
                text("SELECT id, name FROM devices WHERE tenant_id = :tid ORDER BY name"),
                {"tid": self.tenant_id},
            )
        ).all()
        return [DeviceRow(id=r.id, name=r.name) for r in rows]

    async def _ranked(
        self, *, field: str, source: str, frm: datetime, to: datetime,
        device_id: uuid.UUID | None = None, action: str | None = None, limit: int = 10,
    ) -> list[EventTopRow]:
        # `field` MUST be allowlisted (it is interpolated as a column name); everything else is bound.
        if field not in TOP_FIELDS:
            raise ValueError(f"field not allowed: {field}")
        clauses = ["tenant_id = :tid", f"{field} <> ''", "source = :source", "time >= :frm", "time < :to"]
        params: dict = {"tid": self.tenant_id, "source": source, "frm": frm, "to": to, "limit": min(limit, 1000)}
        if device_id is not None:
            clauses.append("device_id = :did")
            params["did"] = device_id
        if action is not None:
            clauses.append("action = :action")
            params["action"] = action
        where = " AND ".join(clauses)
        sql = text(
            f"SELECT {field} AS value, count(*) AS count FROM events WHERE {where} "
            f"GROUP BY {field} ORDER BY count DESC, value LIMIT :limit"
        )
        rows = (await self.session.execute(sql, params)).all()
        return [EventTopRow(value=str(r.value), count=int(r.count)) for r in rows]

    async def top(
        self, *, field: str, frm: datetime, to: datetime, source: str = "ids",
        device_id: uuid.UUID | None = None, limit: int = 10,
    ) -> list[EventTopRow]:
        return await self._ranked(field=field, source=source, frm=frm, to=to, device_id=device_id, limit=limit)

    async def top_blocked_domains(
        self, *, frm: datetime, to: datetime, device_id: uuid.UUID | None = None, limit: int = 10,
    ) -> list[EventTopRow]:
        return await self._ranked(
            field="name", source="dns", frm=frm, to=to, device_id=device_id, action="blocked", limit=limit,
        )

    async def timeline(
        self, *, frm: datetime, to: datetime, bucket: str, source: str = "ids",
        device_id: uuid.UUID | None = None,
    ) -> list[tuple[datetime, int]]:
        if bucket not in _BUCKETS:
            raise ValueError(f"bucket not allowed: {bucket}")
        clauses = ["tenant_id = :tid", "source = :source", "time >= :frm", "time < :to"]
        params: dict = {"tid": self.tenant_id, "source": source, "frm": frm, "to": to}
        if device_id is not None:
            clauses.append("device_id = :did")
            params["did"] = device_id
        where = " AND ".join(clauses)
        # `bucket` is allowlist-validated above; interpolated as a literal interval (asyncpg cannot
        # bind a str as an interval). Everything else is a bound parameter.
        sql = text(
            f"SELECT time_bucket('{bucket}'::interval, time) AS b, count(*) AS c "
            f"FROM events WHERE {where} GROUP BY b ORDER BY b"
        )
        rows = (await self.session.execute(sql, params)).all()
        return [(r.b, int(r.c)) for r in rows]
