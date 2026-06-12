from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import enforce_csrf, require_org
from app.core.rbac import Action
from app.models.user import User
from app.schemas.system import LivePushIn, LivePushOut
from app.services.app_settings import get_live_push, set_live_push
from app.services.audit import AuditService

router = APIRouter(prefix="/api/admin", tags=["system"])


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
