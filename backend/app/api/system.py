from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import enforce_csrf, require_org
from app.core.rbac import Action
from app.models.user import User
from app.schemas.system import (
    LivePushIn,
    LivePushOut,
    RuntimeSettingOut,
    RuntimeSettingsOut,
    RuntimeSettingsPatch,
)
from app.services.app_settings import get_live_push, set_live_push
from app.services.audit import AuditService
from app.services.runtime_settings import (
    active_settings,
    get_runtime_config,
    runtime_defaults,
    update_runtime_config,
)

router = APIRouter(prefix="/api/admin", tags=["system"])


async def _runtime_settings_out(session: AsyncSession) -> RuntimeSettingsOut:
    effective = await get_runtime_config(session)
    defaults = runtime_defaults()
    return RuntimeSettingsOut(
        settings=[
            RuntimeSettingOut(
                key=r.key,
                value=effective[r.key],
                default=defaults[r.key],
                kind=r.kind.__name__,
                minimum=r.minimum,
                maximum=r.maximum,
                group=r.group,
            )
            for r in active_settings()
        ]
    )


@router.get("/settings", response_model=RuntimeSettingsOut)
async def get_runtime_settings(
    user: User = Depends(require_org(Action.SYSTEM_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> RuntimeSettingsOut:
    return await _runtime_settings_out(session)


@router.put("/settings", response_model=RuntimeSettingsOut, dependencies=[Depends(enforce_csrf)])
async def update_runtime_settings(
    body: RuntimeSettingsPatch,
    request: Request,
    user: User = Depends(require_org(Action.SYSTEM_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> RuntimeSettingsOut:
    # Only expose the active settings for editing; an inactive key is treated as unknown (its consumer
    # is not wired yet, so accepting it would be a silent no-op).
    allowed = {r.key for r in active_settings()}
    unknown = sorted(k for k in body.values if k not in allowed)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"unknown setting(s): {', '.join(unknown)}",
        )
    try:
        await update_runtime_config(session, body.values)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    await AuditService(session).record(
        actor_user_id=user.id,
        tenant_id=None,
        action="system.runtime_config",
        ip=request.client.host if request.client else None,
        details={"keys": sorted(body.values)},
    )
    await session.commit()
    return await _runtime_settings_out(session)


@router.get("/live-push", response_model=LivePushOut)
async def get_live_push_setting(
    user: User = Depends(require_org(Action.SYSTEM_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> LivePushOut:
    return LivePushOut(enabled=await get_live_push(session, env_default=get_settings().live_push_enabled))


@router.put("/live-push", response_model=LivePushOut, dependencies=[Depends(enforce_csrf)])
async def set_live_push_setting(
    body: LivePushIn,
    request: Request,
    user: User = Depends(require_org(Action.SYSTEM_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> LivePushOut:
    await set_live_push(session, body.enabled)
    await AuditService(session).record(
        actor_user_id=user.id,
        tenant_id=None,
        action="system.live_push",
        ip=request.client.host if request.client else None,
        details={"enabled": body.enabled},
    )
    await session.commit()
    return LivePushOut(enabled=body.enabled)
