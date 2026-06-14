import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.services.runtime_settings as rs
from app.models.app_setting import AppSetting
from app.services.runtime_settings import (
    RUNTIME_SETTINGS,
    _BY_KEY,
    active_settings,
    get_runtime_config,
    get_runtime_config_or_defaults,
    runtime_defaults,
    update_runtime_config,
)


async def _session(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


def test_registry_covers_the_runtime_settings():
    keys = {r.key for r in RUNTIME_SETTINGS}
    assert keys == {
        "firmware_max_status_polls",
        "firmware_poll_interval_seconds",
        "catalog_auto_fetch",
        "geoip_auto_fetch",
        "silent_alert_enabled",
        "silent_alert_after_hours",
        "login_max_attempts",
        "login_lockout_window_seconds",
        "session_ttl_hours",
        "session_idle_minutes",
        "perimeter_retention_days",
        "events_retention_days",
        "metrics_retention_days",
    }


def test_active_settings_exclude_unwired_consumers():
    # An active setting has its consumer wired (exposed by the admin API). PR2 wired the events/metrics
    # retention purges (purge_timeseries_retention cron), so every registry setting is now active.
    inactive = {r.key for r in RUNTIME_SETTINGS if not r.active}
    assert inactive == set()
    assert {r.key for r in active_settings()} == {r.key for r in RUNTIME_SETTINGS}


def test_perimeter_retention_default_and_override(db_engine):
    assert runtime_defaults()["perimeter_retention_days"] == 30
    assert _BY_KEY["perimeter_retention_days"].active is True
    # events/metrics retention consumers wired in PR2 -> active.
    assert _BY_KEY["events_retention_days"].active is True
    assert _BY_KEY["metrics_retention_days"].active is True


def test_defaults_match_env_settings():
    d = runtime_defaults()
    assert d["session_ttl_hours"] == 12
    assert d["login_max_attempts"] == 5
    assert d["silent_alert_enabled"] is True
    assert d["firmware_poll_interval_seconds"] == 5.0


async def test_get_runtime_config_returns_defaults_when_no_override(db_engine):
    factory = await _session(db_engine)
    async with factory() as s:
        cfg = await get_runtime_config(s)
    assert cfg == runtime_defaults()


async def test_update_then_get_merges_override_over_default(db_engine):
    factory = await _session(db_engine)
    async with factory() as s:
        eff = await update_runtime_config(s, {"session_ttl_hours": 48, "silent_alert_enabled": False})
        await s.commit()
    assert eff["session_ttl_hours"] == 48
    assert eff["silent_alert_enabled"] is False
    # untouched keys keep their default
    assert eff["login_max_attempts"] == runtime_defaults()["login_max_attempts"]

    async with factory() as s:
        cfg = await get_runtime_config(s)
        row = await s.get(AppSetting, "runtime_config")
    assert cfg["session_ttl_hours"] == 48
    assert cfg["silent_alert_enabled"] is False
    # only the overrides are stored, not the whole config
    assert set(row.value) == {"session_ttl_hours", "silent_alert_enabled"}


async def test_update_rejects_unknown_key(db_engine):
    factory = await _session(db_engine)
    async with factory() as s:
        with pytest.raises(ValueError, match="unknown"):
            await update_runtime_config(s, {"not_a_setting": 1})


async def test_update_rejects_wrong_type(db_engine):
    factory = await _session(db_engine)
    async with factory() as s:
        with pytest.raises(ValueError):
            await update_runtime_config(s, {"session_ttl_hours": "lots"})
        with pytest.raises(ValueError):
            await update_runtime_config(s, {"silent_alert_enabled": 1})  # int is not a bool
        with pytest.raises(ValueError):
            await update_runtime_config(s, {"session_ttl_hours": 12.5})  # float is not an int


async def test_update_rejects_out_of_bounds(db_engine):
    factory = await _session(db_engine)
    async with factory() as s:
        with pytest.raises(ValueError, match=">="):
            await update_runtime_config(s, {"session_ttl_hours": 0})


async def test_get_runtime_config_or_defaults_falls_back_on_error(db_engine, monkeypatch):
    # A config-store read failure must degrade to the defaults, never raise into an auth hot path.
    async def boom(session):
        raise RuntimeError("db down")

    monkeypatch.setattr(rs, "get_runtime_config", boom)
    factory = await _session(db_engine)
    async with factory() as s:
        cfg = await get_runtime_config_or_defaults(s)
    assert cfg == runtime_defaults()


async def test_get_ignores_corrupt_stored_value(db_engine):
    factory = await _session(db_engine)
    async with factory() as s:
        s.add(AppSetting(key="runtime_config", value={"session_ttl_hours": "broken", "unknown": 9}))
        await s.commit()
    async with factory() as s:
        cfg = await get_runtime_config(s)
    # corrupt + unknown keys are ignored; defaults stand
    assert cfg["session_ttl_hours"] == runtime_defaults()["session_ttl_hours"]
    assert "unknown" not in cfg
