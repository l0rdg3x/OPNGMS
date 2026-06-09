import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.db import get_session
from app.core.deps import TenantContext, enforce_csrf, require_tenant
from app.core.rbac import Action
from app.models.device import Device
from app.repositories.device import DeviceRepository
from app.schemas.device import DeviceIn, DeviceOut, DeviceUpdateIn, RotateSecretIn, TestResultOut
from app.services.audit import AuditService
from app.services.onboarding import Prober, get_prober

router = APIRouter(prefix="/api/tenants/{tenant_id}/devices", tags=["devices"])


@router.get("", response_model=list[DeviceOut])
async def list_devices(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> list[Device]:
    return list(await DeviceRepository(session, tenant_id).list())


@router.get("/{device_id}", response_model=DeviceOut)
async def get_device(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> Device:
    device = await DeviceRepository(session, tenant_id).get(device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device inesistente")
    return device


@router.post(
    "",
    response_model=DeviceOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_csrf)],
)
async def create_device(
    tenant_id: uuid.UUID,
    payload: DeviceIn,
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_WRITE)),
    session: AsyncSession = Depends(get_session),
    prober: Prober = Depends(get_prober),
) -> Device:
    result = await prober(
        payload.base_url,
        payload.api_key,
        payload.api_secret,
        verify_tls=payload.verify_tls,
        tls_fingerprint=payload.tls_fingerprint,
    )
    device = Device(
        name=payload.name,
        base_url=payload.base_url,
        api_key_enc=crypto.encrypt(payload.api_key),
        api_secret_enc=crypto.encrypt(payload.api_secret),
        verify_tls=payload.verify_tls,
        tls_fingerprint=payload.tls_fingerprint,
        site=payload.site,
        tags=payload.tags,
        status="reachable" if result.reachable else "unverified",
        firmware_version=result.firmware_version,
        last_seen=datetime.now(timezone.utc) if result.reachable else None,
    )
    device = await DeviceRepository(session, tenant_id).add(device)
    await AuditService(session).record(
        actor_user_id=ctx.user.id,
        tenant_id=tenant_id,
        action="device.create",
        target_type="device",
        target_id=str(device.id),
        ip=request.client.host if request.client else None,
        details={"name": device.name, "status": device.status},
    )
    await session.commit()
    return device


@router.patch("/{device_id}", response_model=DeviceOut, dependencies=[Depends(enforce_csrf)])
async def update_device(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    payload: DeviceUpdateIn,
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> Device:
    repo = DeviceRepository(session, tenant_id)
    device = await repo.get(device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device inesistente")
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(device, field, value)
    await session.flush()
    await session.refresh(device)
    await AuditService(session).record(
        actor_user_id=ctx.user.id,
        tenant_id=tenant_id,
        action="device.update",
        target_type="device",
        target_id=str(device.id),
        ip=request.client.host if request.client else None,
        details=changes,
    )
    await session.commit()
    return device


@router.delete(
    "/{device_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(enforce_csrf)],
)
async def delete_device(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> None:
    repo = DeviceRepository(session, tenant_id)
    device = await repo.get(device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device inesistente")
    await repo.delete(device)
    await AuditService(session).record(
        actor_user_id=ctx.user.id,
        tenant_id=tenant_id,
        action="device.delete",
        target_type="device",
        target_id=str(device_id),
        ip=request.client.host if request.client else None,
        details={},
    )
    await session.commit()


@router.post(
    "/{device_id}/test-connection",
    response_model=TestResultOut,
    dependencies=[Depends(enforce_csrf)],
)
async def test_device_connection(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_WRITE)),
    session: AsyncSession = Depends(get_session),
    prober: Prober = Depends(get_prober),
) -> TestResultOut:
    repo = DeviceRepository(session, tenant_id)
    device = await repo.get(device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device inesistente")
    result = await prober(
        device.base_url,
        crypto.decrypt(device.api_key_enc),
        crypto.decrypt(device.api_secret_enc),
        verify_tls=device.verify_tls,
        tls_fingerprint=device.tls_fingerprint,
    )
    device.status = "reachable" if result.reachable else "unverified"
    if result.reachable:
        device.last_seen = datetime.now(timezone.utc)
        device.firmware_version = result.firmware_version
    await session.flush()
    await AuditService(session).record(
        actor_user_id=ctx.user.id,
        tenant_id=tenant_id,
        action="device.test",
        target_type="device",
        target_id=str(device.id),
        ip=request.client.host if request.client else None,
        details={"status": device.status},
    )
    await session.commit()
    return TestResultOut(
        status=device.status, firmware_version=device.firmware_version, error=result.error
    )


@router.post(
    "/{device_id}/rotate-secret",
    response_model=DeviceOut,
    dependencies=[Depends(enforce_csrf)],
)
async def rotate_secret(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    payload: RotateSecretIn,
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> Device:
    repo = DeviceRepository(session, tenant_id)
    device = await repo.get(device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device inesistente")
    device.api_key_enc = crypto.encrypt(payload.api_key)
    device.api_secret_enc = crypto.encrypt(payload.api_secret)
    await session.flush()
    await session.refresh(device)  # ricarica updated_at server-side (evita MissingGreenlet alla serializzazione DeviceOut)
    await AuditService(session).record(
        actor_user_id=ctx.user.id,
        tenant_id=tenant_id,
        action="device.rotate_secret",
        target_type="device",
        target_id=str(device.id),
        ip=request.client.host if request.client else None,
        details={},  # MAI loggare i segreti
    )
    await session.commit()
    return device
