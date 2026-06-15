import logging
from datetime import datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense.client import OpnsenseError
from app.models.device import Device
from app.models.event import Event
from app.models.ingest_cursor import IngestCursor
from app.services.alerting import raise_service_alerts

logger = logging.getLogger(__name__)

# Active sources.
SOURCES = ["ids", "dns", "service"]


async def ingest_events(session: AsyncSession, device: Device, client, now: datetime) -> int:
    """Ingest the events (per source) of a device. Returns the number of new events seen.

    Resilient: an error in one source neither blocks the others nor raises. Idempotent:
    cursor per (device, source) + ON CONFLICT DO NOTHING insert on the dedup PK.

    Side effect: a NEW high-severity service event raises a deduped Alert. Best-effort — an
    alert failure is logged and never aborts the ingest (the events are already persisted).
    """
    total = 0
    new_service_rows: list[dict] = []
    for source in SOURCES:
        try:
            collect = new_service_rows if source == "service" else None
            total += await _ingest_source(session, device, client, source, collect)
        except OpnsenseError:
            continue  # an unavailable source does not block the others
    if new_service_rows:
        try:
            await raise_service_alerts(session, device, new_service_rows)
        except Exception:
            logger.warning("service-event alerting failed for device %s", device.id, exc_info=True)
    return total


async def _ingest_source(
    session: AsyncSession, device: Device, client, source: str, collect: list | None = None
) -> int:
    cursor = await session.get(IngestCursor, (device.id, source))
    since = cursor.last_time if cursor else None
    raw = await _fetch(client, source, since)
    rows = [_normalize(device, source, r) for r in raw]
    if since is not None:
        rows = [r for r in rows if r["time"] > since]  # best-effort client-side
    if not rows:
        return 0
    insert = pg_insert(Event).values(rows).on_conflict_do_nothing()
    if collect is not None:
        # RETURNING yields only the rows actually inserted (not the ones ON CONFLICT skipped), so
        # alerting sees genuinely-new events — never a duplicate that was already stored/alerted.
        result = await session.execute(insert.returning(Event.name, Event.severity))
        collect.extend({"name": name, "severity": severity} for name, severity in result)
    else:
        await session.execute(insert)
    new_max = max(r["time"] for r in rows)
    await _advance_cursor(session, device.id, source, new_max)
    return len(rows)


async def _fetch(client, source: str, since):
    if source == "ids":
        return await client.get_ids_alerts(since)
    if source == "dns":
        return await client.get_dns_events(since)
    if source == "service":
        return await client.get_service_events(since)
    raise ValueError(f"unknown source: {source}")


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
