"""SP-1 PR4c — global retention lowered → superadmin's impacted-tenants feedback.

When a superadmin LOWERS a global retention default via ``PUT /api/admin/settings``, the response carries a
``retention_impacts`` list: each tenant that (a) has NO per-tenant override for the lowered store (so it
follows the global) AND (b) has an enabled, fixed-window schedule whose covered range now exceeds the new
global. Covers: a tenant with no override is impacted; a tenant WITH an override is not (it uses its own
value); raising/unchanged retention or a non-retention setting yields no enumeration (empty list).
"""
import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.repositories.report_schedule import ReportScheduleRepository
from app.repositories.report_settings import ReportSettingsRepository
from app.repositories.tenant_retention import TenantRetentionRepository
from tests.conftest import csrf_headers

PW = "pw12345-secure"


def _sf(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def _superadmin(api_client):
    await api_client.post("/api/setup", json={"email": "sa@x.io", "name": "SA", "password": PW})
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": PW})


async def _seed_tenant(db_engine, slug):
    """A bare tenant (no membership needed — the superadmin drives the global PUT). Returns its id."""
    tid = uuid.uuid4()
    async with _sf(db_engine)() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,:n,:s,'active')"),
                        {"i": tid, "n": slug, "s": slug})
        await s.commit()
    return tid


async def _ensure_settings(db_engine, tid):
    async with _sf(db_engine)() as s:
        await set_tenant_context(s, tid)
        await ReportSettingsRepository(s, tid).upsert(title="R", owner="", timezone="UTC", sections={})
        await s.commit()


async def _add_schedule(db_engine, tid, *, frequency, enabled=True, sections=None):
    async with _sf(db_engine)() as s:
        await set_tenant_context(s, tid)
        await ReportScheduleRepository(s, tid).upsert(
            device_id=None, enabled=enabled, frequency=frequency,
            weekday=0 if frequency == "weekly" else None, hour=4,
            recipients=["a@x.io"], created_by=None, now=datetime.now(UTC), sections=sections,
        )
        await s.commit()


async def _set_override(db_engine, tid, patch):
    async with _sf(db_engine)() as s:
        await set_tenant_context(s, tid)
        await TenantRetentionRepository(s, tid).upsert(patch)
        await s.commit()


async def _put_settings(api_client, values):
    r = await api_client.put("/api/admin/settings", json={"values": values},
                             headers=csrf_headers(api_client))
    assert r.status_code == 200, r.text
    return r.json()["retention_impacts"]


# ── lowering the global below a no-override tenant's monthly need → impacted ───────────────────────

async def test_lowering_global_impacts_tenant_without_override(api_client, db_engine):
    await _superadmin(api_client)
    tid = await _seed_tenant(db_engine, "imp-monthly")
    await _ensure_settings(db_engine, tid)
    await _add_schedule(db_engine, tid, frequency="monthly")  # 30-day window, no override → follows global

    # metrics global 30 -> 14: the monthly schedule (30) now over-runs the new global.
    impacts = await _put_settings(api_client, {"metrics_retention_days": 14})
    assert len(impacts) == 1, impacts
    imp = impacts[0]
    assert imp["tenant_id"] == str(tid)
    assert imp["tenant_name"] == "imp-monthly"
    assert imp["store"] == "metrics"
    assert imp["range_days"] == 30
    assert imp["bound"] == 14


# ── a tenant WITH a metrics override is NOT impacted by the global change ──────────────────────────

async def test_tenant_with_override_is_not_impacted(api_client, db_engine):
    await _superadmin(api_client)
    tid = await _seed_tenant(db_engine, "imp-override")
    await _ensure_settings(db_engine, tid)
    await _add_schedule(db_engine, tid, frequency="monthly")
    # The tenant pins its own metrics retention (generous) → it does not follow the lowered global.
    await _set_override(db_engine, tid, {"metrics": 90})

    impacts = await _put_settings(api_client, {"metrics_retention_days": 14})
    assert impacts == []


async def test_override_tenant_excluded_but_plain_tenant_included(api_client, db_engine):
    """Two tenants, same monthly schedule: only the one WITHOUT an override appears in the impacts."""
    await _superadmin(api_client)
    plain = await _seed_tenant(db_engine, "imp-plain")
    pinned = await _seed_tenant(db_engine, "imp-pinned")
    for tid in (plain, pinned):
        await _ensure_settings(db_engine, tid)
        await _add_schedule(db_engine, tid, frequency="monthly")
    await _set_override(db_engine, pinned, {"metrics": 90})

    impacts = await _put_settings(api_client, {"metrics_retention_days": 14})
    assert len(impacts) == 1 and impacts[0]["tenant_id"] == str(plain)


# ── raising / unchanged retention → no enumeration ────────────────────────────────────────────────

async def test_raising_retention_yields_no_impacts(api_client, db_engine):
    await _superadmin(api_client)
    tid = await _seed_tenant(db_engine, "imp-raise")
    await _ensure_settings(db_engine, tid)
    await _add_schedule(db_engine, tid, frequency="monthly")
    # 30 -> 60 is a RAISE; even though a monthly schedule exists, nothing is now over-long.
    assert await _put_settings(api_client, {"metrics_retention_days": 60}) == []


async def test_unchanged_retention_yields_no_impacts(api_client, db_engine):
    await _superadmin(api_client)
    tid = await _seed_tenant(db_engine, "imp-same")
    await _ensure_settings(db_engine, tid)
    await _add_schedule(db_engine, tid, frequency="monthly")
    # Writing the same value is not a lowering → no scan.
    assert await _put_settings(api_client, {"metrics_retention_days": 30}) == []


# ── a non-retention setting change → no enumeration ───────────────────────────────────────────────

async def test_non_retention_setting_yields_no_impacts(api_client, db_engine):
    await _superadmin(api_client)
    tid = await _seed_tenant(db_engine, "imp-nonret")
    await _ensure_settings(db_engine, tid)
    await _add_schedule(db_engine, tid, frequency="monthly")
    # Lowering an unrelated knob must NOT trigger the impacted-tenants scan.
    assert await _put_settings(api_client, {"login_max_attempts": 3}) == []


# ── RLS: the cross-tenant scan runs correctly under the production opngms_app role ─────────────────

async def test_impacts_scan_under_app_role_rls(app_role_api_client, db_engine):
    """The PUT's cross-tenant scan runs as ``opngms_app`` (RLS ENFORCED) via per-tenant
    ``set_tenant_context`` — not an owner/BYPASSRLS connection. Seed two tenants (one overridden) and assert
    only the no-override one is reported, proving the GUC loop scopes each read to the right tenant under
    production-equivalent RLS (the other tests use the owner-role ``api_client``, which is RLS-exempt)."""
    await _superadmin(app_role_api_client)
    plain = await _seed_tenant(db_engine, "rls-plain")
    pinned = await _seed_tenant(db_engine, "rls-pinned")
    for tid in (plain, pinned):
        await _ensure_settings(db_engine, tid)
        await _add_schedule(db_engine, tid, frequency="monthly")
    await _set_override(db_engine, pinned, {"metrics": 90})

    impacts = await _put_settings(app_role_api_client, {"metrics_retention_days": 14})
    assert len(impacts) == 1 and impacts[0]["tenant_id"] == str(plain)


# ── SP-2: lowering log_lake never enumerates tenants (it is NOT a report-bounding store) ───────────

async def test_lowering_log_lake_yields_no_impacts(api_client, db_engine):
    await _superadmin(api_client)
    tid = await _seed_tenant(db_engine, "imp-loglake")
    await _ensure_settings(db_engine, tid)
    await _add_schedule(db_engine, tid, frequency="monthly")
    # log_lake is NOT a report-bounding store (it stays out of SECTION_STORES) → lowering it to a value
    # that would over-run the monthly schedule still produces no impact and triggers no tenant scan.
    assert await _put_settings(api_client, {"log_lake_retention_days": 1}) == []


# ── precision: a different store lowered does not pull in a metrics-only over-run ──────────────────

async def test_lowering_other_store_does_not_impact_metrics_schedule(api_client, db_engine):
    await _superadmin(api_client)
    tid = await _seed_tenant(db_engine, "imp-otherstore")
    await _ensure_settings(db_engine, tid)
    # A metrics-only monthly schedule (perimeter sections off).
    sections = {"failed_logins": False, "firewall_blocks": False}
    await _add_schedule(db_engine, tid, frequency="monthly", sections=sections)
    # Lower perimeter (not metrics): the schedule's limiting store is metrics @30 >= 30 → no impact.
    assert await _put_settings(api_client, {"perimeter_retention_days": 5}) == []
