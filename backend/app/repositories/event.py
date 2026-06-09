import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.event import EventOut, EventTopRow

# Defensive cap on the number of rows returned by the event list.
MAX_EVENTS = 1000

# Whitelist of columns allowed for top-N aggregation. The `field` becomes a SQL
# column name (cannot be bound), so it MUST be validated against this set.
TOP_FIELDS = frozenset({"src_ip", "dst_ip", "name", "action", "severity"})

_LIST_COLUMNS = "time, device_id, source, category, src_ip, dst_ip, name, severity, action, attributes"


class EventRepository:
    """Tenant-scoped event reads. Double isolation: tenant_id filter + RLS."""

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def list(
        self,
        *,
        source: str | None,
        device_id: uuid.UUID | None,
        frm: datetime | None,
        to: datetime | None,
        limit: int,
    ) -> list[EventOut]:
        clauses = ["tenant_id = :tid"]
        params: dict = {"tid": self.tenant_id, "limit": min(limit, MAX_EVENTS)}
        if source is not None:
            clauses.append("source = :source")
            params["source"] = source
        if device_id is not None:
            clauses.append("device_id = :did")
            params["did"] = device_id
        if frm is not None:
            clauses.append("time >= :frm")
            params["frm"] = frm
        if to is not None:
            clauses.append("time < :to")
            params["to"] = to
        where = " AND ".join(clauses)
        sql = text(
            f"SELECT {_LIST_COLUMNS} FROM events WHERE {where} "
            "ORDER BY time DESC LIMIT :limit"
        )
        rows = (await self.session.execute(sql, params)).mappings().all()
        return [EventOut(**dict(r)) for r in rows]

    async def top(
        self,
        *,
        field: str,
        source: str | None,
        frm: datetime | None,
        to: datetime | None,
        limit: int,
    ) -> list[EventTopRow]:
        if field not in TOP_FIELDS:
            raise ValueError(f"field not allowed: {field}")
        clauses = ["tenant_id = :tid", f"{field} <> ''"]
        params: dict = {"tid": self.tenant_id, "limit": min(limit, MAX_EVENTS)}
        if source is not None:
            clauses.append("source = :source")
            params["source"] = source
        if frm is not None:
            clauses.append("time >= :frm")
            params["frm"] = frm
        if to is not None:
            clauses.append("time < :to")
            params["to"] = to
        where = " AND ".join(clauses)
        # `field` is validated against TOP_FIELDS above (safe to interpolate).
        sql = text(
            f"SELECT {field} AS value, count(*) AS count FROM events WHERE {where} "
            f"GROUP BY {field} ORDER BY count DESC, value LIMIT :limit"
        )
        rows = (await self.session.execute(sql, params)).all()
        return [EventTopRow(value=str(r.value), count=int(r.count)) for r in rows]
