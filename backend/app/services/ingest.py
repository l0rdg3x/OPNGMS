import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense.client import OpnsenseError
from app.models.device import Device
from app.models.event import Event
from app.models.ingest_cursor import IngestCursor
from app.services.alerting import raise_config_audit_alerts, raise_service_alerts

logger = logging.getLogger(__name__)

# Active sources.
SOURCES = ["ids", "dns", "service", "config_audit"]
# Sources whose newly-inserted rows feed ingest-time alerting. The raiser for each is dispatched
# explicitly in ingest_events (a module-level name lookup that tests can still monkeypatch).
_ALERTING_SOURCES = ("service", "config_audit")

_MGMT_CORR_WINDOW = timedelta(minutes=3)


async def _learn_mgmt_ip(session: AsyncSession, device: Device, api_events: list[dict]) -> str | None:
    """OPNGMS's source IP if an api-event correlates (within the window) with an OPNGMS-applied change on
    this device, and the correlated events agree on a single IP; else None (no/ambiguous correlation)."""
    times = [e["time"] for e in api_events]
    lo, hi = min(times) - _MGMT_CORR_WINDOW, max(times) + _MGMT_CORR_WINDOW
    applied = (await session.execute(
        text("SELECT applied_at FROM config_changes WHERE device_id = :d AND status = 'applied' "
             "AND applied_at BETWEEN :lo AND :hi"),
        {"d": device.id, "lo": lo, "hi": hi})).scalars().all()
    if not applied:
        return None
    w = _MGMT_CORR_WINDOW.total_seconds()
    ips = {e["src_ip"] for e in api_events
           if any(abs((e["time"] - a).total_seconds()) <= w for a in applied)}
    return next(iter(ips)) if len(ips) == 1 else None


async def _attribute_mgmt_ip(session: AsyncSession, device: Device, events: list[dict]) -> None:
    """Auto-learn OPNGMS's management IP and reclassify api-channel config-audit changes by actor IP:
    OPNGMS's IP -> 'opngms' (expected), any other IP -> 'api_external' (drift, severity medium -> alerts).
    Mutates `events` in place; a no-op until the IP is learned. Runs before the events are stored."""
    api_events = [e for e in events if e.get("action") == "api" and e.get("src_ip")]
    if not api_events:
        return
    learned = await _learn_mgmt_ip(session, device, api_events)
    if learned and device.mgmt_source_ip != learned:
        device.mgmt_source_ip = learned
    mgmt = device.mgmt_source_ip
    if not mgmt:
        return
    for e in api_events:
        attrs = dict(e.get("attributes", {}))
        if e["src_ip"] == mgmt:
            e["action"] = "opngms"
            attrs["origin"] = "opngms"
        else:
            e["action"] = "api_external"
            e["severity"] = "medium"
            attrs["origin"] = "api_external"
            attrs["drift"] = True
        e["attributes"] = attrs


async def ingest_events(session: AsyncSession, device: Device, client, now: datetime) -> int:
    """Ingest the events (per source) of a device. Returns the number of new events seen.

    Resilient: an error in one source neither blocks the others nor raises. Idempotent:
    cursor per (device, source) + ON CONFLICT DO NOTHING insert on the dedup PK.

    The per-source HTTP fetches are independent, so they run CONCURRENTLY (one round-trip per source at
    once instead of N in series); the database writes stay sequential on the shared, not-concurrency-safe
    session. `return_exceptions=True` keeps the per-source resilience: a source whose fetch errors is
    skipped, the others proceed.

    Side effect: NEW alert-bearing events (a high-severity service event, a direct/drift config change)
    raise a deduped Alert. Best-effort — an alert failure is logged and never aborts the ingest.
    """
    # Phase 1: read every source's cursor (fast local reads) to get its `since` watermark.
    sinces: dict[str, datetime | None] = {}
    for source in SOURCES:
        cursor = await session.get(IngestCursor, (device.id, source))
        sinces[source] = cursor.last_time if cursor else None
    # Phase 2: fetch all sources concurrently (independent HTTP; each call uses its own httpx client).
    raws = await asyncio.gather(
        *(_fetch(client, source, sinces[source]) for source in SOURCES),
        return_exceptions=True,
    )
    # Phase 3: persist each source sequentially on the shared session (in SOURCES order, deterministic).
    total = 0
    new_rows: dict[str, list[dict]] = {src: [] for src in _ALERTING_SOURCES}
    for source, raw in zip(SOURCES, raws, strict=True):
        if isinstance(raw, OpnsenseError):
            continue  # an unavailable source does not block the others
        if isinstance(raw, BaseException):
            raise raw  # an unexpected (non-connector) error is not swallowed
        if source == "config_audit":
            await _attribute_mgmt_ip(session, device, raw)
        total += await _store_source(session, device, source, raw, sinces[source], new_rows.get(source))
    for source, rows in new_rows.items():
        if not rows:
            continue
        try:
            if source == "service":
                await raise_service_alerts(session, device, rows)
            elif source == "config_audit":
                await raise_config_audit_alerts(session, device, rows)
        except Exception:
            logger.warning("%s alerting failed for device %s", source, device.id, exc_info=True)
    return total


async def _store_source(
    session: AsyncSession, device: Device, source: str, raw: list, since: datetime | None,
    collect: list | None = None,
) -> int:
    """Persist one source's already-fetched raw events: normalize, dedup-insert, advance the cursor."""
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
    if source == "config_audit":
        return await client.get_config_changes(since)
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
