"""SP-1 PR4b — retention-side WARN (per-tenant, computed on read).

``GET /api/tenants/{id}/retention`` returns a ``warnings`` list: each ENABLED, fixed-window
(weekly/monthly) report schedule whose covered range now exceeds the tenant's effective retention for its
enabled sections. Lowering retention never blocks — the drift is surfaced here instead. Covers: a monthly
schedule over a sub-30 metrics override warns (naming ``metrics``); a weekly schedule within bound does not;
an on_demand schedule never warns; a disabled schedule is skipped.
"""
import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.repositories.report_schedule import ReportScheduleRepository
from app.repositories.report_settings import ReportSettingsRepository
from app.repositories.tenant_retention import TenantRetentionRepository
from tests.factories import make_membership, make_user

PW = "pw12345-secure"


def _sf(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def _seed_admin(db_engine, slug):
    """A tenant with a tenant_admin + default report settings. Returns the tenant id."""
    tid = uuid.uuid4()
    async with _sf(db_engine)() as s:
        admin = await make_user(s, email=f"adm-{slug}@x.io", password=PW)
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,:s,:s,'active')"),
                        {"i": tid, "s": slug})
        await make_membership(s, user_id=admin.id, tenant_id=tid, role="tenant_admin")
        await s.commit()
    return tid


async def _login(api_client, slug):
    r = await api_client.post("/api/login", json={"email": f"adm-{slug}@x.io", "password": PW})
    assert r.status_code == 200, r.text


async def _set_override(db_engine, tid, patch):
    async with _sf(db_engine)() as s:
        await set_tenant_context(s, tid)
        await TenantRetentionRepository(s, tid).upsert(patch)
        await s.commit()


async def _add_schedule(db_engine, tid, *, frequency, enabled=True, device_id=None,
                        sections=None, weekday=0):
    """Create/upsert a report schedule directly (bypasses the API's BLOCK so we can simulate drift)."""
    async with _sf(db_engine)() as s:
        await set_tenant_context(s, tid)
        await ReportScheduleRepository(s, tid).upsert(
            device_id=device_id, enabled=enabled, frequency=frequency,
            weekday=weekday if frequency == "weekly" else None, hour=4,
            recipients=["a@x.io"], created_by=None, now=datetime.now(UTC), sections=sections,
        )
        await s.commit()


async def _ensure_settings(db_engine, tid):
    """Persist default report settings (so resolve_sections sees the tenant defaults)."""
    async with _sf(db_engine)() as s:
        await set_tenant_context(s, tid)
        await ReportSettingsRepository(s, tid).upsert(
            title="R", owner="", timezone="UTC", sections={},
        )
        await s.commit()


async def _warnings(api_client, tid):
    r = await api_client.get(f"/api/tenants/{tid}/retention")
    assert r.status_code == 200, r.text
    return r.json()["warnings"]


# ── monthly schedule + sub-30 metrics override → one warning naming metrics ───────────────────────

async def test_monthly_schedule_over_short_metrics_warns(api_client, db_engine):
    slug = "warn-monthly"
    tid = await _seed_admin(db_engine, slug)
    await _ensure_settings(db_engine, tid)
    await _add_schedule(db_engine, tid, frequency="monthly")
    # metrics lowered to 14 < monthly window (30): the schedule is now over-long.
    await _set_override(db_engine, tid, {"metrics": 14})
    await _login(api_client, slug)

    warnings = await _warnings(api_client, tid)
    assert len(warnings) == 1, warnings
    w = warnings[0]
    assert w["frequency"] == "monthly"
    assert w["range_days"] == 30
    assert w["bound"] == 14
    assert w["limiting_store"] == "metrics"
    assert uuid.UUID(w["schedule_id"])  # a real schedule id is returned


# ── weekly schedule with a comfortable bound → no warning ─────────────────────────────────────────

async def test_weekly_schedule_within_bound_does_not_warn(api_client, db_engine):
    slug = "warn-weekly-ok"
    tid = await _seed_admin(db_engine, slug)
    await _ensure_settings(db_engine, tid)
    await _add_schedule(db_engine, tid, frequency="weekly")
    # default metrics 30 >= weekly window (7) -> within bound.
    await _login(api_client, slug)
    assert await _warnings(api_client, tid) == []


async def test_weekly_schedule_over_tiny_bound_warns(api_client, db_engine):
    """A weekly schedule DOES warn once retention drops below its 7-day window (drift exists for weekly too)."""
    slug = "warn-weekly-drift"
    tid = await _seed_admin(db_engine, slug)
    await _ensure_settings(db_engine, tid)
    await _add_schedule(db_engine, tid, frequency="weekly")
    await _set_override(db_engine, tid, {"metrics": 3})  # 3 < 7
    await _login(api_client, slug)

    warnings = await _warnings(api_client, tid)
    assert len(warnings) == 1 and warnings[0]["range_days"] == 7 and warnings[0]["bound"] == 3


# ── on_demand → never warns (no fixed window) ─────────────────────────────────────────────────────

async def test_on_demand_schedule_never_warns(api_client, db_engine):
    slug = "warn-ondemand"
    tid = await _seed_admin(db_engine, slug)
    await _ensure_settings(db_engine, tid)
    await _add_schedule(db_engine, tid, frequency="on_demand")
    # crush every store — an on_demand schedule still must not warn.
    await _set_override(db_engine, tid, {"metrics": 1, "events": 1, "perimeter": 1})
    await _login(api_client, slug)
    assert await _warnings(api_client, tid) == []


# ── disabled schedule → skipped even when over-long ───────────────────────────────────────────────

async def test_disabled_schedule_is_skipped(api_client, db_engine):
    slug = "warn-disabled"
    tid = await _seed_admin(db_engine, slug)
    await _ensure_settings(db_engine, tid)
    await _add_schedule(db_engine, tid, frequency="monthly", enabled=False)
    await _set_override(db_engine, tid, {"metrics": 1})  # would warn if it were enabled
    await _login(api_client, slug)
    assert await _warnings(api_client, tid) == []


# ── per-section precision: a perimeter-only schedule isn't bounded by a short metrics retention ────

async def test_perimeter_only_schedule_not_warned_by_short_metrics(api_client, db_engine):
    slug = "warn-precision"
    tid = await _seed_admin(db_engine, slug)
    await _ensure_settings(db_engine, tid)
    sections = {"failed_logins": True, "firewall_blocks": True}
    # disable every other section so only perimeter sources are on
    sections.update(dict.fromkeys(
        ("summary", "health", "alerts_wan", "firmware_config", "attacks", "attacker_countries",
         "web", "data", "status", "applications", "web_filter"), False))
    await _add_schedule(db_engine, tid, frequency="monthly", sections=sections)
    # short metrics but generous perimeter -> perimeter-only schedule is fine
    await _set_override(db_engine, tid, {"metrics": 1, "perimeter": 90})
    await _login(api_client, slug)
    assert await _warnings(api_client, tid) == []


# ── no schedules → empty warnings (the default for a fresh tenant) ────────────────────────────────

async def test_no_schedules_returns_empty_warnings(api_client, db_engine):
    slug = "warn-none"
    tid = await _seed_admin(db_engine, slug)
    await _login(api_client, slug)
    assert await _warnings(api_client, tid) == []
