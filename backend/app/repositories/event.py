import base64
import json
import uuid
import uuid as _uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.event import EventOut, EventTopRow


def encode_cursor(time: datetime, device_id: _uuid.UUID, source: str, event_key: str) -> str:
    raw = json.dumps([time.isoformat(), str(device_id), source, event_key]).encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_cursor(cursor: str) -> tuple[datetime, _uuid.UUID, str, str]:
    try:
        t, did, source, ek = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return datetime.fromisoformat(t), _uuid.UUID(did), source, ek
    except Exception as exc:  # noqa: BLE001
        raise ValueError("invalid cursor") from exc

# Defensive cap on the number of rows returned by the event list.
MAX_EVENTS = 1000

# Allow-list of columns for top-N aggregation. The column name becomes part of the SQL text (it
# cannot be a bound parameter), so the request-supplied `field` is never interpolated directly:
# it is used only as a key into this literal map, and the *value* (a source-code constant) is what
# goes into the query — so the SQL is built exclusively from constants.
_TOP_COLUMN = {
    "src_ip": "src_ip",
    "dst_ip": "dst_ip",
    "name": "name",
    "action": "action",
    "severity": "severity",
}
TOP_FIELDS = frozenset(_TOP_COLUMN)

_LIST_COLUMNS = "time, device_id, source, category, src_ip, dst_ip, name, severity, action, attributes"


class EventRepository:
    """Tenant-scoped event reads. Double isolation: tenant_id filter + RLS."""

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    def _filter_clauses(
        self,
        params: dict,
        *,
        source: str | None,
        device_id: uuid.UUID | None,
        frm: datetime | None,
        to: datetime | None,
    ) -> list[str]:
        """Build the shared tenant/source/device/time WHERE clauses, mutating ``params``."""
        clauses = ["tenant_id = :tid"]
        params["tid"] = self.tenant_id
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
        return clauses

    async def list(
        self,
        *,
        source: str | None,
        device_id: uuid.UUID | None,
        frm: datetime | None,
        to: datetime | None,
        limit: int,
    ) -> list[EventOut]:
        params: dict = {"limit": min(limit, MAX_EVENTS)}
        clauses = self._filter_clauses(params, source=source, device_id=device_id, frm=frm, to=to)
        where = " AND ".join(clauses)
        sql = text(
            f"SELECT {_LIST_COLUMNS} FROM events WHERE {where} "
            "ORDER BY time DESC LIMIT :limit"
        )
        rows = (await self.session.execute(sql, params)).mappings().all()
        return [EventOut(**dict(r)) for r in rows]

    async def list_page(
        self,
        *,
        source: str | None,
        device_id: uuid.UUID | None,
        frm: datetime | None,
        to: datetime | None,
        after: str | None,
        limit: int,
    ) -> tuple[list[EventOut], str | None]:
        n = min(limit, MAX_EVENTS)
        params: dict = {"limit": n}
        clauses = self._filter_clauses(params, source=source, device_id=device_id, frm=frm, to=to)
        if after is not None:
            c_time, c_did, c_source, c_ek = decode_cursor(after)
            clauses.append("(time, device_id, source, event_key) < (:c_time, :c_did, :c_source, :c_ek)")
            params |= {"c_time": c_time, "c_did": c_did, "c_source": c_source, "c_ek": c_ek}
        where = " AND ".join(clauses)
        sql = text(
            f"SELECT {_LIST_COLUMNS}, event_key FROM events WHERE {where} "
            "ORDER BY time DESC, device_id DESC, source DESC, event_key DESC LIMIT :limit"
        )
        rows = (await self.session.execute(sql, params)).mappings().all()
        items = [EventOut(**{k: v for k, v in dict(r).items() if k != "event_key"}) for r in rows]
        next_cursor = None
        if len(rows) == n:
            last = rows[-1]
            next_cursor = encode_cursor(last["time"], last["device_id"], last["source"], last["event_key"])
        return items, next_cursor

    async def top(
        self,
        *,
        field: str,
        source: str | None,
        frm: datetime | None,
        to: datetime | None,
        limit: int,
    ) -> list[EventTopRow]:
        col = _TOP_COLUMN.get(field)
        if col is None:
            raise ValueError(f"field not allowed: {field}")
        # `col` is a literal from _TOP_COLUMN (a source-code constant), never the raw request value.
        clauses = ["tenant_id = :tid", f"{col} <> ''"]
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
        sql = text(
            f"SELECT {col} AS value, count(*) AS count FROM events WHERE {where} "
            f"GROUP BY {col} ORDER BY count DESC, value LIMIT :limit"
        )
        rows = (await self.session.execute(sql, params)).all()
        return [EventTopRow(value=str(r.value), count=int(r.count)) for r in rows]
