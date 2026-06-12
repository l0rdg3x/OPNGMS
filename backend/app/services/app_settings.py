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
