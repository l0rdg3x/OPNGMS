"""Superadmin cross-tenant log-fleet aggregates (the only cross-tenant views in the console)."""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import set_tenant_context
from app.models.device import Device
from app.models.device_log_forwarding import DeviceLogForwarding
from app.repositories.tenant import TenantRepository

logger = logging.getLogger(__name__)


async def fleet_forwarding_counts(session: AsyncSession) -> dict[uuid.UUID, dict]:
    """Per-tenant device-forwarding counts. Lists tenants (the tenants table is not RLS-scoped), then
    for each tenant sets the RLS context and counts — no bypass role. Returns {tenant_id: {...}}."""
    tenants = await TenantRepository(session).list()
    out: dict[uuid.UUID, dict] = {}
    for t in tenants:
        await set_tenant_context(session, t.id)
        rows = (await session.execute(
            select(DeviceLogForwarding.enabled, DeviceLogForwarding.revoked_at)
        )).all()
        enabled = sum(1 for e, _ in rows if e)
        revoked = sum(1 for e, r in rows if not e and r is not None)
        disabled = sum(1 for e, r in rows if not e and r is None)
        total_devices = (await session.execute(select(func.count()).select_from(Device))).scalar_one()
        out[t.id] = {
            "tenant_name": t.name,
            "enabled": enabled,
            "disabled": disabled,
            "revoked": revoked,
            "total_devices": int(total_devices),
        }
    return out


STALE_AFTER = timedelta(hours=1)


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


async def fleet_log_stats(settings, *, window_hours: int = 24) -> dict[str, dict]:
    """Per-tenant {last_log_at, volume} via ONE OpenSearch terms agg on tenant_id (NO tenant
    filter — superadmin-only, aggregates only). The volume counts docs within the last
    ``window_hours`` (24h/7d/30d). Best-effort: returns {} on any OpenSearch error."""
    body = {
        "size": 0,
        "aggs": {"by_tenant": {
            "terms": {"field": "tenant_id", "size": settings.log_fleet_terms_size},
            "aggs": {
                "last_log": {"max": {"field": "@timestamp"}},
                "last_24h": {"filter": {"range": {"@timestamp": {"gte": f"now-{window_hours}h"}}}},
            },
        }},
    }
    url = f"{settings.opensearch_url}/opngms-logs-*/_search"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, params={"ignore_unavailable": "true"}, json=body)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return {}
    by_tenant = data.get("aggregations", {}).get("by_tenant", {})
    # Don't silently truncate: a non-zero sum_other_doc_count means tenants beyond the terms cap were
    # dropped (they'd show as spuriously "silent"). Surface it instead of hiding it.
    if (by_tenant.get("sum_other_doc_count") or 0) > 0:
        logger.warning(
            "log-fleet terms agg truncated at size=%d; some tenants are missing volume/last-log — "
            "raise LOG_FLEET_TERMS_SIZE", settings.log_fleet_terms_size)
    out: dict[str, dict] = {}
    for b in by_tenant.get("buckets", []):
        out[str(b.get("key", ""))] = {
            "last_log_at": (b.get("last_log", {}) or {}).get("value_as_string"),
            "volume": (b.get("last_24h", {}) or {}).get("doc_count"),
        }
    return out


async def fleet_device_log_stats(settings, tenant_id: uuid.UUID, *, window_hours: int = 24) -> dict[str, dict]:
    """Per-DEVICE {last_log_at, volume} for ONE tenant via a single OpenSearch terms agg on
    ``device_id``, filtered to ``tenant_id``. Best-effort: returns {} on any OpenSearch error."""
    body = {
        "size": 0,
        "query": {"bool": {"filter": [{"term": {"tenant_id": str(tenant_id)}}]}},
        "aggs": {"by_device": {
            "terms": {"field": "device_id", "size": settings.log_fleet_terms_size},
            "aggs": {
                "last_log": {"max": {"field": "@timestamp"}},
                "in_window": {"filter": {"range": {"@timestamp": {"gte": f"now-{window_hours}h"}}}},
            },
        }},
    }
    url = f"{settings.opensearch_url}/opngms-logs-*/_search"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, params={"ignore_unavailable": "true"}, json=body)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return {}
    by_device = data.get("aggregations", {}).get("by_device", {})
    if (by_device.get("sum_other_doc_count") or 0) > 0:
        logger.warning(
            "log-fleet per-device terms agg truncated at size=%d for tenant %s; some devices are "
            "missing volume/last-log — raise LOG_FLEET_TERMS_SIZE", settings.log_fleet_terms_size, tenant_id)
    out: dict[str, dict] = {}
    for b in by_device.get("buckets", []):
        out[str(b.get("key", ""))] = {
            "last_log_at": (b.get("last_log", {}) or {}).get("value_as_string"),
            "volume": (b.get("in_window", {}) or {}).get("doc_count"),
        }
    return out


def _forwarding_status(enabled: bool | None, revoked_at) -> str:
    """Map a device's DeviceLogForwarding state to a label (None = no forwarding row)."""
    if revoked_at is not None:
        return "revoked"
    if enabled:
        return "enabled"
    if enabled is False:
        return "disabled"
    return "none"


async def tenant_device_fleet(session: AsyncSession, settings, *, tenant_id: uuid.UUID,
                              window_hours: int = 24) -> dict:
    """Per-DEVICE drill-down for one tenant: every device + its forwarding status, last log and
    windowed volume, with a per-device 'silent' flag (forwarding enabled but no recent log).

    Sets the RLS context to ``tenant_id`` and reads devices under it (no bypass role), so this is
    safe for the superadmin to call for any tenant from the org-level fleet endpoint."""
    await set_tenant_context(session, tenant_id)
    device_rows = (await session.execute(
        select(Device.id, Device.name, DeviceLogForwarding.enabled, DeviceLogForwarding.revoked_at)
        .outerjoin(DeviceLogForwarding, DeviceLogForwarding.device_id == Device.id)
        .order_by(Device.name)
    )).all()
    stats = await fleet_device_log_stats(settings, tenant_id, window_hours=window_hours)
    now = datetime.now(UTC)
    devices: list[dict] = []
    silent = enabled_devices = volume_total = 0
    for did, name, enabled, revoked_at in device_rows:
        st = stats.get(str(did), {})
        last_dt = _parse_iso(st["last_log_at"]) if st.get("last_log_at") else None
        vol = st.get("volume")
        forwarding = _forwarding_status(enabled, revoked_at)
        is_forwarding = forwarding == "enabled"
        is_silent = is_forwarding and (last_dt is None or (now - last_dt) > STALE_AFTER)
        if is_forwarding:
            enabled_devices += 1
        volume_total += vol or 0
        if is_silent:
            silent += 1
        devices.append({
            "device_id": did, "name": name, "forwarding": forwarding,
            "last_log_at": last_dt, "volume": vol, "is_silent": is_silent,
        })
    return {"devices": devices, "totals": {
        "enabled_devices": enabled_devices, "silent_devices": silent, "volume": volume_total}}


async def log_fleet_overview(session: AsyncSession, settings, *, window_hours: int = 24) -> dict:
    """Combine the relational forwarding counts with the OpenSearch log stats into per-tenant rows +
    totals. A tenant is 'silent' when it has enabled devices but no recent log. The per-tenant +
    total ``volume`` count the logs within the last ``window_hours``."""
    counts = await fleet_forwarding_counts(session)
    stats = await fleet_log_stats(settings, window_hours=window_hours)
    now = datetime.now(UTC)
    rows: list[dict] = []
    silent = enabled_devices = volume_total = with_fwd = 0
    for tid, c in counts.items():
        st = stats.get(str(tid), {})
        last_dt = _parse_iso(st["last_log_at"]) if st.get("last_log_at") else None
        vol = st.get("volume")
        rows.append({
            "tenant_id": tid, "tenant_name": c["tenant_name"],
            "enabled": c["enabled"], "disabled": c["disabled"], "revoked": c["revoked"],
            "total_devices": c["total_devices"], "last_log_at": last_dt, "volume": vol,
        })
        enabled_devices += c["enabled"]
        volume_total += vol or 0
        if c["enabled"] > 0:
            with_fwd += 1
            if last_dt is None or (now - last_dt) > STALE_AFTER:
                silent += 1
    rows.sort(key=lambda r: r["tenant_name"])
    return {"tenants": rows, "totals": {
        "tenants_with_forwarding": with_fwd, "enabled_devices": enabled_devices,
        "volume": volume_total, "silent_tenants": silent}}
