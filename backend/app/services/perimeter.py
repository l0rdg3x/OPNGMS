"""Perimeter ingest: poll failed logins + firewall blocks, UPSERT a bounded per-(device, kind, src_ip)
rollup (NOT per-packet events). Reuses IngestCursor; resilient (one source's error never blocks the
other). The rollup feeds the Overview cards, the /perimeter page, and the report sections.
"""
import contextlib
from datetime import datetime, timedelta

from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense.client import OpnsenseError
from app.models.device import Device
from app.models.ingest_cursor import IngestCursor
from app.models.perimeter_attacker import PerimeterAttacker

# (kind == IngestCursor.source, client getter). kind matches the perimeter_attacker.kind column.
_KINDS = [("firewall_block", "get_firewall_blocks"), ("login_failed", "get_auth_failures")]
RETENTION_DAYS = 30


async def ingest_perimeter(session: AsyncSession, device: Device, client, now: datetime) -> int:
    """Ingest both perimeter signals for a device. Returns the count of new observations.

    Resilient: an unavailable source (OpnsenseError) does not block the other or raise."""
    total = 0
    for kind, getter in _KINDS:
        with contextlib.suppress(OpnsenseError):
            total += await _ingest_kind(session, device, client, kind, getter)
    return total


async def _ingest_kind(session: AsyncSession, device: Device, client, kind: str, getter: str) -> int:
    cursor = await session.get(IngestCursor, (device.id, kind))
    since = cursor.last_time if cursor else None
    rows = await getattr(client, getter)()
    rows = [r for r in rows if r.get("time") and (since is None or r["time"] > since)]
    if not rows:
        return 0
    by_ip: dict[str, list] = {}
    for r in rows:
        by_ip.setdefault(r["src_ip"], []).append(r)
    for ip, group in by_ip.items():
        await _upsert(session, device, kind, ip, group)
    await _advance(session, device.id, kind, max(r["time"] for r in rows))
    return len(rows)


def _detail(kind: str, group: list) -> dict:
    """Compact, kind-specific rollup detail (capped). The query layer ranks; this is for display."""
    if kind == "firewall_block":
        ports = sorted({str(r["attributes"].get("dstport")) for r in group if r["attributes"].get("dstport")})
        return {"top_ports": ports[:10]}
    users = sorted({r["attributes"].get("username") for r in group if r["attributes"].get("username")})
    return {"usernames": users[:10], "last_username": group[-1]["attributes"].get("username")}


async def _upsert(session: AsyncSession, device: Device, kind: str, ip: str, group: list) -> None:
    times = [r["time"] for r in group]
    detail = _detail(kind, group)
    stmt = (
        pg_insert(PerimeterAttacker)
        .values(device_id=device.id, tenant_id=device.tenant_id, kind=kind, src_ip=ip,
                count=len(group), first_seen=min(times), last_seen=max(times), detail=detail)
        .on_conflict_do_update(
            index_elements=["device_id", "kind", "src_ip"],
            set_={
                "count": PerimeterAttacker.count + len(group),
                "last_seen": func.greatest(PerimeterAttacker.last_seen, max(times)),
                # Keep the latest batch's compact detail (the query layer ranks across rows; an exact
                # cumulative union is out of scope for v1).
                "detail": detail,
            },
        )
    )
    await session.execute(stmt)


async def _advance(session: AsyncSession, device_id, kind: str, new_time: datetime) -> None:
    stmt = (
        pg_insert(IngestCursor)
        .values(device_id=device_id, source=kind, last_time=new_time)
        .on_conflict_do_update(index_elements=["device_id", "source"], set_={"last_time": new_time})
    )
    await session.execute(stmt)


async def purge_perimeter(session: AsyncSession, now: datetime) -> int:
    """Retention sweep: drop rollup rows not seen within RETENTION_DAYS. Returns rows deleted."""
    cutoff = now - timedelta(days=RETENTION_DAYS)
    res = await session.execute(delete(PerimeterAttacker).where(PerimeterAttacker.last_seen < cutoff))
    return res.rowcount or 0
