import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.app_setting import AppSetting
from app.services.runtime_settings import (
    RUNTIME_SETTINGS,
    active_settings,
    get_runtime_config,
    runtime_defaults,
    update_runtime_config,
)


async def _session(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


def test_registry_covers_the_ten_runtime_settings():
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
    }


def test_auth_settings_are_inactive_for_now():
    # session/login consumers are wired in a follow-up PR; they must not be exposed for editing yet.
    inactive = {r.key for r in RUNTIME_SETTINGS if not r.active}
    assert inactive == {
        "login_max_attempts",
        "login_lockout_window_seconds",
        "session_ttl_hours",
        "session_idle_minutes",
    }
    active = {r.key for r in active_settings()}
    assert active.isdisjoint(inactive)
    assert "silent_alert_after_hours" in active and len(active) == 6


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
