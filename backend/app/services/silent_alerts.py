"""Detect tenants gone silent (enabled forwarding but no recent logs) and reconcile the global
SilentTenantAlert state: create + email once on entry, delete on recovery, dedup in between.

Runs under the worker's OWNER session (RLS-exempt), so it does NOT reuse the RLS-scoped
log_fleet.fleet_forwarding_counts; it counts enabled forwarding with an explicit cross-tenant
GROUP BY (correct regardless of RLS).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device_log_forwarding import DeviceLogForwarding
from app.models.silent_tenant_alert import SilentTenantAlert
from app.repositories.tenant import TenantRepository
from app.services.log_fleet import _parse_iso, fleet_log_stats
from app.services.runtime_settings import get_runtime_config


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


async def detect_and_alert(session: AsyncSession, settings) -> dict:
    """Reconcile the silent-tenant alert state (DB only — flush, no commit). Returns the summary plus
    `newly_silent` (the tenants that just entered the silent state). The CALLER must commit and THEN
    email `newly_silent`: committing before sending guarantees a post-email commit failure can't make
    the next run re-insert + re-email the same episode (dedup survives). Owner session."""
    # The master switch + silence threshold are runtime-tunable from the System page.
    runtime = await get_runtime_config(session)
    after_hours = runtime["silent_alert_after_hours"]
    if not runtime["silent_alert_enabled"]:
        return {"silent": 0, "new": 0, "recovered": 0, "after_hours": after_hours, "newly_silent": []}
    enabled = await enabled_forwarding_by_tenant(session)
    names = {t.id: t.name for t in await TenantRepository(session).list()}
    stats = await fleet_log_stats(settings, window_hours=after_hours)
    now = datetime.now(UTC)
    silent = compute_silent_tenants(enabled, names, stats, now=now,
                                    threshold_hours=after_hours)
    existing = {row.tenant_id: row for row in
                (await session.execute(select(SilentTenantAlert))).scalars().all()}
    recovered = [row for tid, row in existing.items() if tid not in silent]
    # ON CONFLICT DO NOTHING + RETURNING: race-safe (a concurrent run can't raise IntegrityError),
    # and only rows WE actually inserted are reported (an already-present row returns nothing -> dedup).
    inserted: list[uuid.UUID] = []
    for tid in (t for t in silent if t not in existing):
        stmt = (pg_insert(SilentTenantAlert)
                .values(id=uuid.uuid4(), tenant_id=tid, tenant_name=silent[tid]["tenant_name"],
                        silent_since=now, last_alert_at=now)
                .on_conflict_do_nothing(index_elements=["tenant_id"])
                .returning(SilentTenantAlert.tenant_id))
        if (await session.execute(stmt)).scalar_one_or_none() is not None:
            inserted.append(tid)
    for row in recovered:
        await session.delete(row)
    await session.flush()
    return {"silent": len(silent), "new": len(inserted), "recovered": len(recovered),
            "after_hours": after_hours,
            "newly_silent": [(tid, silent[tid]["tenant_name"]) for tid in inserted]}
