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


async def fleet_log_stats(settings) -> dict[str, dict]:
    """Per-tenant {last_log_at, volume_24h} via ONE OpenSearch terms agg on tenant_id (NO tenant
    filter — superadmin-only, aggregates only). Best-effort: returns {} on any OpenSearch error."""
    body = {
        "size": 0,
        "aggs": {"by_tenant": {
            "terms": {"field": "tenant_id", "size": settings.log_fleet_terms_size},
            "aggs": {
                "last_log": {"max": {"field": "@timestamp"}},
                "last_24h": {"filter": {"range": {"@timestamp": {"gte": "now-24h"}}}},
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
            "volume_24h": (b.get("last_24h", {}) or {}).get("doc_count"),
        }
    return out


async def log_fleet_overview(session: AsyncSession, settings) -> dict:
    """Combine the relational forwarding counts with the OpenSearch log stats into per-tenant rows +
    totals. A tenant is 'silent' when it has enabled devices but no recent log."""
    counts = await fleet_forwarding_counts(session)
    stats = await fleet_log_stats(settings)
    now = datetime.now(UTC)
    rows: list[dict] = []
    silent = enabled_devices = volume_total = with_fwd = 0
    for tid, c in counts.items():
        st = stats.get(str(tid), {})
        last_dt = _parse_iso(st["last_log_at"]) if st.get("last_log_at") else None
        vol = st.get("volume_24h")
        rows.append({
            "tenant_id": tid, "tenant_name": c["tenant_name"],
            "enabled": c["enabled"], "disabled": c["disabled"], "revoked": c["revoked"],
            "total_devices": c["total_devices"], "last_log_at": last_dt, "volume_24h": vol,
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
        "volume_24h": volume_total, "silent_tenants": silent}}
