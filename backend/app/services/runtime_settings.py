"""Runtime-configurable settings: env/code default + a single DB override row.

A small declarative registry over the existing `app_setting` key/value store. One row (key
``runtime_config``) holds *only* the operator's overrides as JSONB; any absent key falls back to its
`Settings` (env) default. Superadmin reads/writes through ``/api/admin/settings``. Every consumer reads
the effective value at use-time, so a change applies without a restart.

Defaults preserve current behavior — these settings only change the *source* of a value (DB-or-env),
never the logic that uses it.
"""
import logging
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.app_setting import AppSetting

logger = logging.getLogger(__name__)

_RUNTIME_KEY = "runtime_config"


@dataclass(frozen=True)
class RuntimeSetting:
    key: str
    kind: type  # int | float | bool
    default: Callable[[Settings], object]  # reads the env/code default off Settings
    minimum: float | None = None
    maximum: float | None = None
    group: str = ""
    # `active` gates whether the setting is exposed by the admin API. A setting whose consumer is not
    # yet wired to read the runtime value is marked inactive so the UI never shows a knob that silently
    # does nothing; it is flipped on in the same change that wires its consumer.
    active: bool = True


# The runtime-safe settings. `default` reads the matching Settings field (the env default); the bounds
# guard an operator typo from wedging a consumer. Cadence/boot-time fields are intentionally absent
# (they are read at import/startup and live in .env).
RUNTIME_SETTINGS: list[RuntimeSetting] = [
    RuntimeSetting("firmware_max_status_polls", int, lambda s: s.firmware_max_status_polls, 1, 100_000, "firmware"),
    RuntimeSetting("firmware_poll_interval_seconds", float, lambda s: s.firmware_poll_interval_seconds, 0.1, 600.0, "firmware"),
    RuntimeSetting("catalog_auto_fetch", bool, lambda s: s.catalog_auto_fetch, group="distribution"),
    RuntimeSetting("geoip_auto_fetch", bool, lambda s: s.geoip_auto_fetch, group="distribution"),
    RuntimeSetting("silent_alert_enabled", bool, lambda s: s.silent_alert_enabled, group="maintenance"),
    RuntimeSetting("silent_alert_after_hours", int, lambda s: s.silent_alert_after_hours, 1, 720, "maintenance"),
    RuntimeSetting("login_max_attempts", int, lambda s: s.login_max_attempts, 1, 50, "security_login"),
    RuntimeSetting("login_lockout_window_seconds", int, lambda s: s.login_lockout_window_seconds, 1, 86_400, "security_login"),
    RuntimeSetting("session_ttl_hours", int, lambda s: s.session_ttl_hours, 1, 8760, "security_session"),
    RuntimeSetting("session_idle_minutes", int, lambda s: s.session_idle_minutes, 1, 525_600, "security_session"),
]

_BY_KEY: dict[str, RuntimeSetting] = {r.key: r for r in RUNTIME_SETTINGS}


def active_settings() -> list[RuntimeSetting]:
    """The settings the admin API exposes (consumer wired). Inactive ones are still defaulted/merged
    internally but never offered for editing."""
    return [r for r in RUNTIME_SETTINGS if r.active]


def _coerce(r: RuntimeSetting, value: object) -> object:
    """Validate `value` against the registry entry's type + bounds. Raises ValueError on mismatch."""
    if isinstance(value, bool):
        # bool is a subclass of int — handle it first so a bool can't satisfy an int/float field.
        if r.kind is bool:
            return value
        raise ValueError(f"{r.key} must be a {r.kind.__name__}")
    if r.kind is bool:
        raise ValueError(f"{r.key} must be a boolean")
    if r.kind is int:
        if not isinstance(value, int):
            raise ValueError(f"{r.key} must be an integer")
        v: float = value
    else:  # float
        if not isinstance(value, (int, float)):
            raise ValueError(f"{r.key} must be a number")
        v = float(value)
    if r.minimum is not None and v < r.minimum:
        raise ValueError(f"{r.key} must be >= {r.minimum}")
    if r.maximum is not None and v > r.maximum:
        raise ValueError(f"{r.key} must be <= {r.maximum}")
    return r.kind(v)


def runtime_defaults() -> dict:
    """The effective config when nothing is overridden (every key present, typed)."""
    s = get_settings()
    return {r.key: r.kind(r.default(s)) for r in RUNTIME_SETTINGS}


async def _load_overrides(session: AsyncSession) -> dict:
    row = (
        await session.execute(select(AppSetting).where(AppSetting.key == _RUNTIME_KEY))
    ).scalar_one_or_none()
    return dict(row.value) if row and isinstance(row.value, dict) else {}


async def get_runtime_config(session: AsyncSession) -> dict:
    """Effective config = registry defaults with the stored overrides merged on top.

    Defensive: an unknown or corrupt stored value is ignored (the default stands) rather than raising,
    so a bad row can never take a consumer down.
    """
    cfg = runtime_defaults()
    for key, value in (await _load_overrides(session)).items():
        r = _BY_KEY.get(key)
        if r is None:
            continue
        try:
            cfg[key] = _coerce(r, value)
        except ValueError:
            continue  # keep the default
    return cfg


async def get_runtime_config_or_defaults(session: AsyncSession) -> dict:
    """Like `get_runtime_config` but NEVER raises: on a config-store read failure (e.g. a transient DB
    fault), fall back to the env/code defaults. Use on hot/auth paths where a hiccup reading the single
    override row must not take the path offline — `runtime_defaults()` is pure in-memory and can't fail.
    The only thing lost on fallback is the operator's DB override, which reverts to the env default.
    """
    try:
        return await get_runtime_config(session)
    except Exception:  # noqa: BLE001 — degrade gracefully; the defaults are always safe
        logger.warning("runtime config read failed; using env/code defaults", exc_info=True)
        return runtime_defaults()


async def update_runtime_config(session: AsyncSession, patch: dict) -> dict:
    """Validate + apply a partial override patch; return the new effective config. Does not commit.

    Raises ValueError on an empty patch, an unknown key, or a type/bounds violation.
    """
    if not isinstance(patch, dict) or not patch:
        raise ValueError("empty patch")
    overrides = await _load_overrides(session)
    for key, value in patch.items():
        r = _BY_KEY.get(key)
        if r is None:
            raise ValueError(f"unknown setting: {key}")
        overrides[key] = _coerce(r, value)
    # Drop any stale keys that are no longer in the registry before persisting.
    overrides = {k: v for k, v in overrides.items() if k in _BY_KEY}
    row = (
        await session.execute(select(AppSetting).where(AppSetting.key == _RUNTIME_KEY))
    ).scalar_one_or_none()
    if row is None:
        session.add(AppSetting(key=_RUNTIME_KEY, value=overrides))
    else:
        row.value = overrides
    return {**runtime_defaults(), **overrides}
