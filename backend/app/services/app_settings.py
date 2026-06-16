"""Global key/value app settings (non-tenant). Currently: the MFA enforcement policy."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting

_MFA_KEY = "mfa_required"
MFA_MODES = {"off", "all", "privileged"}


async def get_mfa_policy(session: AsyncSession) -> str:
    row = (await session.execute(select(AppSetting).where(AppSetting.key == _MFA_KEY))).scalar_one_or_none()
    mode = (row.value or {}).get("mode") if row else None
    return mode if mode in MFA_MODES else "off"


async def set_mfa_policy(session: AsyncSession, mode: str) -> None:
    if mode not in MFA_MODES:
        raise ValueError(f"invalid mfa policy: {mode!r}")
    row = (await session.execute(select(AppSetting).where(AppSetting.key == _MFA_KEY))).scalar_one_or_none()
    if row is None:
        session.add(AppSetting(key=_MFA_KEY, value={"mode": mode}))
    else:
        row.value = {"mode": mode}


_LIVE_PUSH_KEY = "live_push_enabled"


async def get_live_push(session: AsyncSession, *, env_default: bool) -> bool:
    row = (await session.execute(select(AppSetting).where(AppSetting.key == _LIVE_PUSH_KEY))).scalar_one_or_none()
    if row is None:
        return env_default
    return bool((row.value or {}).get("enabled", env_default))


async def set_live_push(session: AsyncSession, enabled: bool) -> None:
    row = (await session.execute(select(AppSetting).where(AppSetting.key == _LIVE_PUSH_KEY))).scalar_one_or_none()
    if row is None:
        session.add(AppSetting(key=_LIVE_PUSH_KEY, value={"enabled": bool(enabled)}))
    else:
        row.value = {"enabled": bool(enabled)}


# WebAuthn relying-party config (string settings; env default + a single DB override row). The numeric
# runtime_settings registry is int/float/bool-only, so these strings follow the get_live_push pattern.
_WEBAUTHN_KEY = "webauthn_config"


async def get_webauthn_settings(
    session: AsyncSession, *, rp_id_default: str, rp_name_default: str, origin_default: str
) -> dict[str, str]:
    """Effective rp_id/rp_name/origin: the DB override (if present) else the env/code default."""
    row = (
        await session.execute(select(AppSetting).where(AppSetting.key == _WEBAUTHN_KEY))
    ).scalar_one_or_none()
    stored = (row.value or {}) if row else {}
    return {
        "rp_id": str(stored.get("rp_id") or rp_id_default or ""),
        "rp_name": str(stored.get("rp_name") or rp_name_default or "OPNGMS"),
        "origin": str(stored.get("origin") or origin_default or ""),
    }


async def set_webauthn_settings(
    session: AsyncSession, *, rp_id: str, rp_name: str, origin: str
) -> None:
    value = {"rp_id": rp_id.strip(), "rp_name": rp_name.strip(), "origin": origin.strip()}
    row = (
        await session.execute(select(AppSetting).where(AppSetting.key == _WEBAUTHN_KEY))
    ).scalar_one_or_none()
    if row is None:
        session.add(AppSetting(key=_WEBAUTHN_KEY, value=value))
    else:
        row.value = value
