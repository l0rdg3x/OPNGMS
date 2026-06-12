"""Detect tenants gone silent (enabled forwarding but no recent logs) and reconcile the global
SilentTenantAlert state: create + email once on entry, delete on recovery, dedup in between.

Runs under the worker's OWNER session (RLS-exempt), so it does NOT reuse the RLS-scoped
log_fleet.fleet_forwarding_counts; it counts enabled forwarding with an explicit cross-tenant
GROUP BY (correct regardless of RLS).
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device_log_forwarding import DeviceLogForwarding
from app.models.silent_tenant_alert import SilentTenantAlert
from app.repositories.tenant import TenantRepository
from app.services.log_fleet import _parse_iso, fleet_log_stats

# Given the newly-silent tenants [(tenant_id, tenant_name), ...], return True iff an email was sent.
SendAlert = Callable[[list[tuple[uuid.UUID, str]]], Awaitable[bool]]


async def enabled_forwarding_by_tenant(session: AsyncSession) -> dict[uuid.UUID, int]:
    """{tenant_id: count of enabled, non-revoked forwarding devices}. Explicit GROUP BY — owner-safe."""
    rows = (await session.execute(
        select(DeviceLogForwarding.tenant_id, func.count())
        .where(DeviceLogForwarding.enabled.is_(True), DeviceLogForwarding.revoked_at.is_(None))
        .group_by(DeviceLogForwarding.tenant_id)
    )).all()
    return {tid: int(n) for tid, n in rows}


def compute_silent_tenants(enabled: dict[uuid.UUID, int], names: dict[uuid.UUID, str],
                           stats: dict[str, dict], *, now: datetime, threshold_hours: int) -> dict[uuid.UUID, dict]:
    """Pure: a tenant is silent if it has enabled forwarding but no log within `threshold_hours`."""
    threshold = timedelta(hours=threshold_hours)
    out: dict[uuid.UUID, dict] = {}
    for tid, n in enabled.items():
        if n <= 0:
            continue
        st = stats.get(str(tid), {})
        last = _parse_iso(st["last_log_at"]) if st.get("last_log_at") else None
        if last is None or (now - last) > threshold:
            out[tid] = {"tenant_name": names.get(tid, ""), "last_log_at": last}
    return out


async def detect_and_alert(session: AsyncSession, settings, *, send_alert: SendAlert) -> dict:
    """Reconcile the silent-tenant alert state and email the newly-silent ones (once). Owner session."""
    if not settings.silent_alert_enabled:
        return {"silent": 0, "new": 0, "recovered": 0, "emailed": False}
    enabled = await enabled_forwarding_by_tenant(session)
    names = {t.id: t.name for t in await TenantRepository(session).list()}
    stats = await fleet_log_stats(settings, window_hours=settings.silent_alert_after_hours)
    now = datetime.now(UTC)
    silent = compute_silent_tenants(enabled, names, stats, now=now,
                                    threshold_hours=settings.silent_alert_after_hours)
    existing = {row.tenant_id: row for row in
                (await session.execute(select(SilentTenantAlert))).scalars().all()}
    new_ids = [tid for tid in silent if tid not in existing]
    recovered = [row for tid, row in existing.items() if tid not in silent]
    for tid in new_ids:
        session.add(SilentTenantAlert(tenant_id=tid, tenant_name=silent[tid]["tenant_name"],
                                      silent_since=now, last_alert_at=now))
    for row in recovered:
        await session.delete(row)
    await session.flush()
    emailed = False
    if new_ids:
        emailed = await send_alert([(tid, silent[tid]["tenant_name"]) for tid in new_ids])
    return {"silent": len(silent), "new": len(new_ids), "recovered": len(recovered), "emailed": emailed}
