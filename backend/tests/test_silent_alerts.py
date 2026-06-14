import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.models.silent_tenant_alert import SilentTenantAlert
from app.services.runtime_settings import update_runtime_config
from app.services.silent_alerts import compute_silent_tenants, detect_and_alert

_NOW = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)


class _S:
    opensearch_url = "http://opensearch:9200"
    log_fleet_terms_size = 10000
    silent_alert_enabled = True
    silent_alert_after_hours = 6


# --- pure compute ---

def test_compute_silent_flags_enabled_without_recent_logs():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    enabled = {a: 2, b: 1, c: 0}                       # c has no enabled forwarding
    names = {a: "Acme", b: "Beta", c: "Gamma"}
    stats = {str(b): {"last_log_at": (_NOW - timedelta(minutes=5)).isoformat()}}  # b fresh
    silent = compute_silent_tenants(enabled, names, stats, now=_NOW, threshold_hours=6)
    assert set(silent) == {a}                          # a enabled + no logs; b fresh; c not enabled
    assert silent[a]["tenant_name"] == "Acme"


def test_compute_silent_respects_threshold():
    a = uuid.uuid4()
    enabled = {a: 1}
    stats = {str(a): {"last_log_at": (_NOW - timedelta(hours=3)).isoformat()}}  # 3h old
    assert compute_silent_tenants(enabled, {a: "A"}, stats, now=_NOW, threshold_hours=6) == {}      # within 6h
    assert set(compute_silent_tenants(enabled, {a: "A"}, stats, now=_NOW, threshold_hours=2)) == {a}  # beyond 2h


# --- detect_and_alert state machine (DB) ---

async def _seed_enabled_tenant(s, *, name, slug):
    tid, did = uuid.uuid4(), uuid.uuid4()
    await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,:n,:sl,'active')"),
                    {"i": tid, "n": name, "sl": slug})
    await set_tenant_context(s, tid)
    await s.execute(text(
        "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
        "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
    await s.execute(text(
        "INSERT INTO device_log_forwarding (device_id,tenant_id,enabled,cert_serial,cert_fingerprint) "
        "VALUES (:d,:t,true,'s','f')"), {"d": did, "t": tid})
    return tid


async def test_detect_creates_alerts_emails_once_and_recovers(db_engine, monkeypatch):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ta = await _seed_enabled_tenant(s, name="Acme", slug="acme")   # will be silent
        tb = await _seed_enabled_tenant(s, name="Beta", slug="beta")   # fresh logs
        await s.commit()

    fresh = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()

    async def fake_stats(settings, *, window_hours=24):
        # Beta has a fresh log; Acme is absent from the agg -> no logs -> silent.
        return {str(tb): {"last_log_at": fresh}}

    monkeypatch.setattr("app.services.silent_alerts.fleet_log_stats", fake_stats)

    # detect_and_alert reconciles state + returns newly_silent (the CALLER emails, after commit).
    async with factory() as s:
        r1 = await detect_and_alert(s, _S())
        await s.commit()
    assert r1["new"] == 1
    assert [name for _id, name in r1["newly_silent"]] == ["Acme"]

    # second run: Acme still silent -> NO new row, nothing to email (dedup)
    async with factory() as s:
        r2 = await detect_and_alert(s, _S())
        await s.commit()
        rows = (await s.execute(select(SilentTenantAlert))).scalars().all()
    assert r2["new"] == 0 and r2["newly_silent"] == []
    assert len(rows) == 1 and rows[0].tenant_name == "Acme"

    # Acme recovers (now has fresh logs) -> its alert row is deleted
    async def fake_stats_recovered(settings, *, window_hours=24):
        return {str(ta): {"last_log_at": fresh}, str(tb): {"last_log_at": fresh}}

    monkeypatch.setattr("app.services.silent_alerts.fleet_log_stats", fake_stats_recovered)
    async with factory() as s:
        r3 = await detect_and_alert(s, _S())
        await s.commit()
        rows = (await s.execute(select(SilentTenantAlert))).scalars().all()
    assert r3["recovered"] == 1 and rows == [] and r3["newly_silent"] == []


async def test_detect_disabled_is_noop(db_engine, monkeypatch):
    called = {"stats": False}

    async def fake_stats(settings, *, window_hours=24):
        called["stats"] = True
        return {}

    monkeypatch.setattr("app.services.silent_alerts.fleet_log_stats", fake_stats)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    # The master switch is now a runtime setting (DB override over the env default).
    async with factory() as s:
        await update_runtime_config(s, {"silent_alert_enabled": False})
        await s.commit()
    async with factory() as s:
        r = await detect_and_alert(s, _S())
    assert r == {"silent": 0, "new": 0, "recovered": 0, "after_hours": 6, "newly_silent": []}
    assert called["stats"] is False  # short-circuits before any work
