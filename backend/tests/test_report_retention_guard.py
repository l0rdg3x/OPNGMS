"""SP-1 PR4a — report-side retention BLOCK.

Covers the bound helper (min effective retention over the stores the enabled sections read; None when no
retention-bounded section is on; a per-tenant override moves the bound; per-section precision) and the two
config-side blocks: on-demand ``POST /reports`` (400) and ``PUT /report-schedules`` (422).
"""
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.repositories.tenant_retention import TenantRetentionRepository
from app.services.report_retention import SECTION_STORES, report_range_bound
from tests.conftest import csrf_headers
from tests.factories import make_membership, make_tenant, make_user

PW = "pw12345-secure"


# ── Bound helper (defaults: perimeter 30, events 90, metrics 30) ──────────────────────────────────

def _sf(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def _tenant(db_engine, slug):
    async with _sf(db_engine)() as s:
        t = await make_tenant(s, slug=slug)
        await s.commit()
        return t.id


def _sections(**on):
    """All section keys False except those passed True."""
    return {k: bool(on.get(k, False)) for k in SECTION_STORES}


async def test_section_store_map_is_complete_and_bounded():
    # Every store referenced is one of the three SP-1 retention stores (or none).
    allowed = {"perimeter", "events", "metrics"}
    for stores in SECTION_STORES.values():
        assert set(stores) <= allowed


async def test_bound_is_min_over_enabled_section_stores(db_engine):
    tid = await _tenant(db_engine, "b-min")
    async with _sf(db_engine)() as s:
        await set_tenant_context(s, tid)
        # events(90) + metrics(30) enabled -> min == 30
        assert await report_range_bound(s, tid, _sections(attacks=True, health=True)) == 30
        # only events(90) -> 90
        assert await report_range_bound(s, tid, _sections(attacks=True)) == 90
        # summary uses events+metrics -> 30
        assert await report_range_bound(s, tid, _sections(summary=True)) == 30


async def test_bound_none_when_no_retention_bounded_section(db_engine):
    tid = await _tenant(db_engine, "b-none")
    async with _sf(db_engine)() as s:
        await set_tenant_context(s, tid)
        # firmware_config maps to () -> no bound
        assert await report_range_bound(s, tid, _sections(firmware_config=True)) is None
        # nothing enabled at all -> no bound
        assert await report_range_bound(s, tid, _sections()) is None


async def test_tenant_override_changes_the_bound(db_engine):
    tid = await _tenant(db_engine, "b-override")
    async with _sf(db_engine)() as s:
        await set_tenant_context(s, tid)
        # baseline events bound = 90
        assert await report_range_bound(s, tid, _sections(attacks=True)) == 90
        await TenantRetentionRepository(s, tid).upsert({"events": 10})
        await s.commit()
    async with _sf(db_engine)() as s:
        await set_tenant_context(s, tid)
        assert await report_range_bound(s, tid, _sections(attacks=True)) == 10


async def test_per_section_precision_perimeter_only_not_bounded_by_short_metrics(db_engine):
    """A report with ONLY perimeter sections is bounded by perimeter retention, not a short metrics one."""
    tid = await _tenant(db_engine, "b-precision")
    async with _sf(db_engine)() as s:
        await set_tenant_context(s, tid)
        await TenantRetentionRepository(s, tid).upsert({"metrics": 3, "perimeter": 60})
        await s.commit()
    async with _sf(db_engine)() as s:
        await set_tenant_context(s, tid)
        # perimeter-only -> bound is perimeter(60), NOT the short metrics(3)
        assert await report_range_bound(
            s, tid, _sections(failed_logins=True, firewall_blocks=True)
        ) == 60
        # add a metrics section and the short metrics retention now dominates
        assert await report_range_bound(
            s, tid, _sections(failed_logins=True, health=True)
        ) == 3


# ── On-demand POST /reports (400) ─────────────────────────────────────────────────────────────────

async def _login_sa(api_client, db_engine, slug):
    tid = await _tenant(db_engine, slug)
    await api_client.post("/api/setup", json={"email": "sa@x.io", "name": "SA", "password": PW})
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": PW})
    return tid


async def _set_override(db_engine, tid, patch):
    async with _sf(db_engine)() as s:
        await set_tenant_context(s, tid)
        await TenantRetentionRepository(s, tid).upsert(patch)
        await s.commit()


async def test_on_demand_blocks_range_over_bound(api_client, db_engine):
    tid = await _login_sa(api_client, db_engine, "od-block")
    # default sections include metrics-bounded ones -> bound 30; lower metrics to 7 to make it tight
    await _set_override(db_engine, tid, {"metrics": 7})
    to = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
    body = {"from": (to - timedelta(days=10)).isoformat(), "to": to.isoformat()}
    r = await api_client.post(f"/api/tenants/{tid}/reports", json=body, headers=csrf_headers(api_client))
    assert r.status_code == 400, r.text
    assert "metrics" in r.json()["detail"]


async def test_on_demand_allows_range_within_bound(api_client, db_engine):
    tid = await _login_sa(api_client, db_engine, "od-ok")
    await _set_override(db_engine, tid, {"metrics": 7})
    to = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
    body = {"from": (to - timedelta(days=5)).isoformat(), "to": to.isoformat()}  # 5 <= 7
    r = await api_client.post(f"/api/tenants/{tid}/reports", json=body, headers=csrf_headers(api_client))
    assert r.status_code == 200, r.text
    assert r.content[:5] == b"%PDF-"


# ── Schedule PUT /report-schedules (422) ──────────────────────────────────────────────────────────

async def _seed_admin(db_engine, slug):
    tid = uuid.uuid4()
    async with _sf(db_engine)() as s:
        admin = await make_user(s, email="adm@x.io", password=PW)
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,:s,:s,'active')"),
                        {"i": tid, "s": slug})
        await make_membership(s, user_id=admin.id, tenant_id=tid, role="tenant_admin")
        await s.commit()
    return tid


async def _login(api_client, email=" adm@x.io"):
    r = await api_client.post("/api/login", json={"email": email.strip(), "password": PW})
    assert r.status_code == 200, r.text


def _schedule_body(frequency, weekday=0):
    return {
        "device_id": None, "enabled": True, "frequency": frequency,
        "weekday": weekday if frequency == "weekly" else None, "hour": 4,
        "recipients": ["a@x.io"],
    }


async def test_schedule_monthly_blocked_when_bound_below_31(api_client, db_engine):
    tid = await _seed_admin(db_engine, "sch-monthly")
    await _set_override(db_engine, tid, {"metrics": 14})  # bound 14 < monthly window
    await _login(api_client)
    r = await api_client.put(f"/api/tenants/{tid}/report-schedules",
                             headers=csrf_headers(api_client), json=_schedule_body("monthly"))
    assert r.status_code == 422, r.text
    assert "metrics" in r.json()["detail"]


async def test_schedule_weekly_ok_when_bound_at_least_7(api_client, db_engine):
    tid = await _seed_admin(db_engine, "sch-weekly")
    # default metrics 30 >= 7 -> weekly OK
    await _login(api_client)
    r = await api_client.put(f"/api/tenants/{tid}/report-schedules",
                             headers=csrf_headers(api_client), json=_schedule_body("weekly"))
    assert r.status_code == 200, r.text


async def test_schedule_monthly_ok_at_default_metrics_30(api_client, db_engine):
    """Out-of-the-box: default metrics retention 30 must NOT block a monthly schedule (treated as 30)."""
    tid = await _seed_admin(db_engine, "sch-default")
    await _login(api_client)
    r = await api_client.put(f"/api/tenants/{tid}/report-schedules",
                             headers=csrf_headers(api_client), json=_schedule_body("monthly"))
    assert r.status_code == 200, r.text


async def test_schedule_on_demand_skips_the_check(api_client, db_engine):
    """on_demand has no fixed window -> the retention check is skipped even with a tiny retention."""
    tid = await _seed_admin(db_engine, "sch-ondemand")
    await _set_override(db_engine, tid, {"metrics": 1, "events": 1, "perimeter": 1})
    await _login(api_client)
    r = await api_client.put(f"/api/tenants/{tid}/report-schedules",
                             headers=csrf_headers(api_client), json=_schedule_body("on_demand"))
    assert r.status_code == 200, r.text


async def test_schedule_perimeter_only_not_blocked_by_short_metrics(api_client, db_engine):
    """Per-section precision at the schedule layer: a perimeter-only monthly schedule is fine when only
    metrics retention is short."""
    tid = await _seed_admin(db_engine, "sch-precision")
    await _set_override(db_engine, tid, {"metrics": 1, "perimeter": 90})
    await _login(api_client)
    body = _schedule_body("monthly")
    body["sections"] = dict.fromkeys(SECTION_STORES, False)
    body["sections"].update({"failed_logins": True, "firewall_blocks": True})
    r = await api_client.put(f"/api/tenants/{tid}/report-schedules",
                             headers=csrf_headers(api_client), json=body)
    assert r.status_code == 200, r.text
