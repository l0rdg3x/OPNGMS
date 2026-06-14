"""Report ↔ retention consistency: the report-side BLOCK (SP-1 PR4a).

A report must never be configured to cover more days than the tenant's effective retention for the
stores its enabled sections read — otherwise it would request already-purged data. The bound for a given
report is the **minimum** effective retention across the retention-bounded stores its enabled sections use
(``effective = per-tenant override ?? global default``, via the SP-1 resolver in ``services/retention``).

This module is the pure mapping + the bound helper; the actual 400/422 blocks live on the report-config
endpoints (``POST /reports`` and ``PUT /report-schedules``). The retention-side WARN is PR4b, not here.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.tenant_retention import TenantRetentionRepository
from app.services.retention import effective_retention_days
from app.services.runtime_settings import get_runtime_config

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
