"""Report ↔ retention consistency: the report-side BLOCK (SP-1 PR4a) + retention-side WARN (PR4b).

A report must never be configured to cover more days than the tenant's effective retention for the
stores its enabled sections read — otherwise it would request already-purged data. The bound for a given
report is the **minimum** effective retention across the retention-bounded stores its enabled sections use
(``effective = per-tenant override ?? global default``, via the SP-1 resolver in ``services/retention``).

This module is the pure mapping + the bound helper. Enforcement is asymmetric: the report side BLOCKS
(the 400/422 on ``POST /reports`` and ``PUT /report-schedules``), while the retention side only WARNS
(:func:`schedule_retention_warnings`, computed on read by ``GET /retention``) — lowering retention is
always allowed, the drift is surfaced, never silently clamped.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.report_schedule import ReportSchedule
from app.repositories.report_settings import ReportSettingsRepository
from app.repositories.tenant_retention import TenantRetentionRepository
from app.services.report_schedule import MONTHLY, ON_DEMAND, WEEKLY
from app.services.reporting.sections import resolve_sections
from app.services.retention import effective_retention_days
from app.services.runtime_settings import get_runtime_config

# Fixed-window days a scheduled run covers, for the retention check. Weekly = the prior 7 days.
# Monthly uses 30 (not the max month length of 31): the prior calendar month is 28-31 days, and the
# default metrics retention is exactly 30 — treating monthly as 31 would block a monthly schedule under
# the out-of-the-box defaults (surprising). 30 keeps the default config consistent; only a metrics
# retention LOWERED below 30 blocks a monthly schedule. A 31-day month with metrics kept at 30 renders
# its single oldest day as "no data" (the spec's accepted empty-period behavior — never a clamp).
# on_demand has no fixed window, so it has no entry here (the callers skip it). Centralized so both the
# report-side BLOCK (``api/report_schedules``) and the retention-side WARN below share one source of truth.
SCHEDULE_RANGE_DAYS: dict[str, int] = {WEEKLY: 7, MONTHLY: 30}

# Section key -> the retention-bounded store(s) its report builder reads. Only the three SP-1 stores
# (perimeter / events / metrics) bound a report; sections backed by tables that are NOT retention-purged
# (alerts, config_changes, devices) contribute no bound and so map to () or only their bounded inputs.
#
# Each mapping is verified against the aggregator the section's builder calls in
# ``services/reporting/context.py`` (+ ``aggregation.py``):
#   failed_logins/firewall_blocks -> aggregator.perimeter_top()        -> perimeter_attacker  (perimeter)
#   attacks                        -> aggregator.timeline/top()         -> events              (events)
#   attacker_countries             -> aggregator.attacker_countries()   -> events              (events)
#   applications/web_filter        -> deterministic MOCK today; the real feed will be events-backed, so
#                                     they carry the events bound (future-proof, matches the spec table)
#   web                            -> aggregator.timeline/top/top_blocked_domains(source='dns') -> events
#                                     (the spec table said metrics; CORRECTED to events — it reads the
#                                     events hypertable, not metrics)
#   health                         -> aggregator.health_summary()       -> metrics             (metrics)
#   data                           -> aggregator.bandwidth_*()          -> metrics             (metrics)
#   status                         -> aggregator.availability_series()  -> metrics             (metrics)
#   summary                        -> aggregator.kpis()                 -> events + metrics    (both)
#   alerts_wan                     -> aggregator.alerts_in_range (alerts table, NOT purged) +
#                                     gateway_quality/vpn_status -> metrics  => bound by metrics only
#   firmware_config                -> aggregator.config_changes_in_range (config_changes, NOT purged) => ()
SECTION_STORES: dict[str, tuple[str, ...]] = {
    "failed_logins": ("perimeter",),
    "firewall_blocks": ("perimeter",),
    "attacks": ("events",),
    "attacker_countries": ("events",),
    "applications": ("events",),
    "web_filter": ("events",),
    "web": ("events",),
    "health": ("metrics",),
    "data": ("metrics",),
    "status": ("metrics",),
    "summary": ("events", "metrics"),
    "alerts_wan": ("metrics",),
    "firmware_config": (),
}


def stores_for_sections(enabled_sections: dict[str, bool]) -> set[str]:
    """Union of the retention-bounded stores the enabled (True) sections read."""
    used: set[str] = set()
    for section, on in enabled_sections.items():
        if on:
            used.update(SECTION_STORES.get(section, ()))
    return used


async def _effective_by_store(
    session: AsyncSession, tenant_id: uuid.UUID, stores: set[str]
) -> dict[str, int]:
    """Each store's effective retention (per-tenant override over the global default) for this tenant.

    Runs on the request session (RLS-scoped to ``tenant_id``); reads only this tenant's override row and
    the global runtime config.
    """
    cfg = await get_runtime_config(session)
    overrides = await TenantRetentionRepository(session, tenant_id).get_overrides()
    return {
        store: effective_retention_days(
            store, global_default=int(cfg[f"{store}_retention_days"]), tenant_override=overrides
        )
        for store in stores
    }


async def report_range_bound(
    session: AsyncSession, tenant_id: uuid.UUID, enabled_sections: dict[str, bool]
) -> int | None:
    """The max days a report with these enabled sections may cover for this tenant, or None if unbounded.

    The bound is the minimum effective retention across the stores the enabled sections use; None when no
    retention-bounded section is enabled (the report draws nothing that gets purged).
    """
    stores = stores_for_sections(enabled_sections)
    if not stores:
        return None
    return min((await _effective_by_store(session, tenant_id, stores)).values())


async def limiting_store_for_sections(
    session: AsyncSession, tenant_id: uuid.UUID, enabled_sections: dict[str, bool]
) -> tuple[str, int] | None:
    """The (store, days) pair that sets the bound (min effective retention) — for error messages.

    None when no retention-bounded section is enabled (mirrors :func:`report_range_bound` returning None).
    """
    stores = stores_for_sections(enabled_sections)
    if not stores:
        return None
    by_store = await _effective_by_store(session, tenant_id, stores)
    return min(by_store.items(), key=lambda p: p[1])


async def schedule_retention_warnings(
    session: AsyncSession, tenant_id: uuid.UUID
) -> list[dict]:
    """Per-tenant retention-side WARN (SP-1 PR4b), computed on read.

    For each ENABLED, fixed-window (``frequency != on_demand``) report schedule of this tenant whose covered
    range now exceeds the current bound for its enabled sections, yield one warning dict
    ``{schedule_id, frequency, range_days, bound, limiting_store}``. Returns ``[]`` when every schedule is
    within its bound. This is the mirror of the report-side BLOCK: an existing schedule can become over-long
    when retention is later lowered, and the block only stops NEW configs — so we surface the drift here.

    Runs on the REQUEST session (RLS-scoped to ``tenant_id``); the schedule query is tenant-filtered and the
    bound reads only this tenant's override row + the global runtime config.
    """
    schedules = list((await session.execute(
        select(ReportSchedule).where(
            ReportSchedule.tenant_id == tenant_id,
            ReportSchedule.enabled.is_(True),
            ReportSchedule.frequency != ON_DEMAND,
        ).order_by(ReportSchedule.device_id.nullsfirst())
    )).scalars().all())
    if not schedules:
        return []
    settings = await ReportSettingsRepository(session, tenant_id).get_or_default()
    # The effective retention inputs (global config + this tenant's override row) are invariant across the
    # tenant's schedules — read them ONCE, then resolve each schedule's bound purely (no per-schedule DB).
    cfg = await get_runtime_config(session)
    overrides = await TenantRetentionRepository(session, tenant_id).get_overrides()

    def _bound(enabled: dict[str, bool]) -> tuple[str, int] | None:
        stores = stores_for_sections(enabled)
        if not stores:
            return None
        by_store = {
            store: effective_retention_days(
                store, global_default=int(cfg[f"{store}_retention_days"]), tenant_override=overrides
            )
            for store in stores
        }
        return min(by_store.items(), key=lambda p: p[1])

    warnings: list[dict] = []
    for schedule in schedules:
        range_days = SCHEDULE_RANGE_DAYS.get(schedule.frequency)
        if range_days is None:  # unknown/forward-compat frequency — nothing to compare against
            continue
        limiting = _bound(resolve_sections(settings.sections, schedule.sections))
        if limiting is not None and range_days > limiting[1]:
            store, bound = limiting
            warnings.append({
                "schedule_id": schedule.id,
                "frequency": schedule.frequency,
                "range_days": range_days,
                "bound": bound,
                "limiting_store": store,
            })
    return warnings
