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
