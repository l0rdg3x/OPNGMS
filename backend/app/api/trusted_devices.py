import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import enforce_csrf, get_current_user
from app.models.user import User
from app.schemas.mfa import TrustedDeviceOut
from app.services.audit import AuditService
from app.services.trusted_device import TrustedDeviceService

router = APIRouter(prefix="/api", tags=["trusted-devices"])


@router.get("/me/trusted-devices", response_model=list[TrustedDeviceOut])
async def list_trusted_devices(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[TrustedDeviceOut]:
    rows = await TrustedDeviceService(session).list_for_user(user.id)
    return [TrustedDeviceOut.model_validate(r) for r in rows]


@router.delete(
    "/me/trusted-devices/{device_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(enforce_csrf)],
)
async def revoke_trusted_device(
    device_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    removed = await TrustedDeviceService(session).revoke(device_id, user.id)
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await AuditService(session).record(
        actor_user_id=user.id, tenant_id=None, action="auth.trusted_device.revoke",
        target_type="user", target_id=str(user.id), ip=None, details={"device": str(device_id)},
    )
    await session.commit()


@router.delete(
    "/me/trusted-devices",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(enforce_csrf)],
)
async def revoke_all_trusted_devices(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    n = await TrustedDeviceService(session).revoke_all(user.id)
    await AuditService(session).record(
        actor_user_id=user.id, tenant_id=None, action="auth.trusted_device.revoke_all",
        target_type="user", target_id=str(user.id), ip=None, details={"count": n},
    )
    await session.commit()
