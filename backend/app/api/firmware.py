import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense.client import OpnsenseError
from app.core.db import get_session
from app.core.deps import TenantContext, enforce_csrf, require_tenant
from app.core.queue import get_enqueuer
from app.core.rbac import Action
from app.models.device import Device
from app.models.firmware_action import FirmwareAction
from app.schemas.firmware import FirmwareActionIn, FirmwareActionOut, FirmwareCheckOut
from app.services.audit import AuditService
from app.services.device_client import client_for_device
from app.services.firmware_action import major_offered, to_int

router = APIRouter(prefix="/api/tenants/{tenant_id}", tags=["firmware"])


async def _device_or_404(session: AsyncSession, tenant_id: uuid.UUID, device_id: uuid.UUID) -> Device:
    device = await session.get(Device, device_id)
    if device is None or device.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return device


@router.post("/devices/{device_id}/firmware/check", response_model=FirmwareCheckOut,
             dependencies=[Depends(enforce_csrf)])
async def firmware_check(
    tenant_id: uuid.UUID, device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> FirmwareCheckOut:
    device = await _device_or_404(session, tenant_id, device_id)
    client = client_for_device(device)
    try:
        await client.firmware_check()
        st = await client.firmware_status_raw()
    except OpnsenseError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=type(exc).__name__) from exc
    return FirmwareCheckOut(
        status=str(st.get("status", "")),
        updates=to_int(st.get("updates")),
        download_size=str(st.get("download_size", "")),
        needs_reboot=str(st.get("upgrade_needs_reboot", "")) in ("1", "true", "True"),
        new_major=major_offered(st),
    )


@router.post("/devices/{device_id}/firmware/action", response_model=FirmwareActionOut,
             status_code=status.HTTP_201_CREATED, dependencies=[Depends(enforce_csrf)])
async def create_firmware_action(
    tenant_id: uuid.UUID, device_id: uuid.UUID, body: FirmwareActionIn,
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.CONFIG_PUSH)),
    session: AsyncSession = Depends(get_session),
    enqueue=Depends(get_enqueuer),
) -> FirmwareActionOut:
    await _device_or_404(session, tenant_id, device_id)
    if body.kind in ("plugin_install", "plugin_remove") and not body.target:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="target (plugin name) required")
    if body.kind in ("firmware_update", "firmware_upgrade") and body.target:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="target not allowed for firmware actions")
    action = FirmwareAction(
        tenant_id=tenant_id,
        device_id=device_id,
        created_by=ctx.user.id,
        kind=body.kind,
        target=body.target,
        scheduled_at=body.scheduled_at,
        status="scheduled",
    )
    session.add(action)
    await session.flush()
    await enqueue("run_firmware_action", str(action.id), defer_until=body.scheduled_at)
    await AuditService(session).record(
        actor_user_id=ctx.user.id, tenant_id=tenant_id, action="device.firmware.action",
        target_type="device", target_id=str(device_id),
        ip=request.client.host if request.client else None,
        details={"kind": body.kind, "target": body.target,
                 "scheduled_at": str(body.scheduled_at) if body.scheduled_at else None},
    )
    await session.commit()
    await session.refresh(action)
    return FirmwareActionOut.model_validate(action)


@router.get("/devices/{device_id}/firmware/actions", response_model=list[FirmwareActionOut])
async def list_firmware_actions(
    tenant_id: uuid.UUID, device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> list[FirmwareActionOut]:
    await _device_or_404(session, tenant_id, device_id)
    rows = (await session.execute(
        select(FirmwareAction)
        .where(FirmwareAction.device_id == device_id, FirmwareAction.tenant_id == tenant_id)
        .order_by(FirmwareAction.created_at.desc()).limit(50)
    )).scalars().all()
    return [FirmwareActionOut.model_validate(r) for r in rows]
