from datetime import datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense.client import OpnsenseError
from app.models.device import Device
from app.models.event import Event
from app.models.ingest_cursor import IngestCursor

# Sorgenti attive.
SOURCES = ["ids", "dns"]


async def ingest_events(session: AsyncSession, device: Device, client, now: datetime) -> int:
    """Ingerisce gli eventi (per source) di un device. Ritorna il n. di eventi nuovi visti.

    Resiliente: l'errore di una source non blocca le altre né solleva. Idempotente:
    cursore per (device, source) + insert ON CONFLICT DO NOTHING sulla PK di dedup.
    """
    total = 0
    for source in SOURCES:
        try:
            total += await _ingest_source(session, device, client, source)
        except OpnsenseError:
            continue  # una source non disponibile non blocca le altre
    return total


async def _ingest_source(session: AsyncSession, device: Device, client, source: str) -> int:
    cursor = await session.get(IngestCursor, (device.id, source))
    since = cursor.last_time if cursor else None
    raw = await _fetch(client, source, since)
    rows = [_normalize(device, source, r) for r in raw]
    if since is not None:
        rows = [r for r in rows if r["time"] > since]  # best-effort client-side
    if not rows:
        return 0
    await session.execute(pg_insert(Event).values(rows).on_conflict_do_nothing())
    new_max = max(r["time"] for r in rows)
    await _advance_cursor(session, device.id, source, new_max)
    return len(rows)


async def _fetch(client, source: str, since):
    if source == "ids":
        return await client.get_ids_alerts(since)
    if source == "dns":
        return await client.get_dns_events(since)
    raise ValueError(f"source sconosciuta: {source}")


def _normalize(device: Device, source: str, r: dict) -> dict:
    return {
        "time": r["time"],
        "device_id": device.id,
        "tenant_id": device.tenant_id,
        "source": source,
        "category": r.get("category", ""),
        "src_ip": r.get("src_ip", ""),
        "dst_ip": r.get("dst_ip", ""),
        "name": r.get("name", ""),
        "severity": r.get("severity", ""),
        "action": r.get("action", ""),
        "event_key": r["event_key"],
        "attributes": r.get("attributes", {}),
    }


async def _advance_cursor(session: AsyncSession, device_id, source: str, new_time: datetime) -> None:
    stmt = (
        pg_insert(IngestCursor)
        .values(device_id=device_id, source=source, last_time=new_time)
        .on_conflict_do_update(
            index_elements=["device_id", "source"],
            set_={"last_time": new_time},
        )
    )
    await session.execute(stmt)
