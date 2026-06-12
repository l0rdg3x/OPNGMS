import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense.client import OpnsenseClient, OpnsenseError
from app.core import crypto
from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import TenantContext, enforce_csrf, require_tenant
from app.core.rbac import Action
from app.models.device import Device
from app.repositories.device_log_forwarding import DeviceLogForwardingRepository
from app.schemas.log_forwarding import LogForwardingOut, RevokeIn
from app.services.audit import AuditService
from app.services.log_forwarding import (
    deprovision_device,
    provision_device,
    revoke_device,
    rotate_device_cert,
)
from app.services.log_search import latest_log_at

router = APIRouter(prefix="/api/tenants/{tenant_id}/devices/{device_id}/log-forwarding",
                   tags=["log-forwarding"])


def _client(device: Device) -> OpnsenseClient:
    return OpnsenseClient(device.base_url, crypto.decrypt(device.api_key_enc),
                          crypto.decrypt(device.api_secret_enc), verify_tls=device.verify_tls,
                          tls_fingerprint=device.tls_fingerprint)


def _out(row, *, device_id: uuid.UUID) -> LogForwardingOut:
    if row is None:
        return LogForwardingOut(device_id=device_id, enabled=False, cert_serial="",
                                cert_fingerprint="", provisioned_at=None)
    return LogForwardingOut(device_id=row.device_id, enabled=row.enabled, cert_serial=row.cert_serial,
                            cert_fingerprint=row.cert_fingerprint, provisioned_at=row.provisioned_at,
                            cert_not_after=row.cert_not_after, revoked_at=row.revoked_at)


async def _device(session, tenant_id, device_id) -> Device:
    device = await session.get(Device, device_id)
    if device is None or device.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return device


@router.get("", response_model=LogForwardingOut)
async def status_log_forwarding(
    tenant_id: uuid.UUID, device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> LogForwardingOut:
    await _device(session, tenant_id, device_id)
    row = await DeviceLogForwardingRepository(session, tenant_id).get(device_id)
    out = _out(row, device_id=device_id)
    if row is not None and row.enabled:
        out.last_log_at = await latest_log_at(get_settings(), tenant_id=tenant_id, device_id=device_id)
    return out


@router.post("/enable", response_model=LogForwardingOut, dependencies=[Depends(enforce_csrf)])
async def enable_log_forwarding(
    tenant_id: uuid.UUID, device_id: uuid.UUID, request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.CONFIG_PUSH)),
    session: AsyncSession = Depends(get_session),
) -> LogForwardingOut:
    device = await _device(session, tenant_id, device_id)
    s = get_settings()
    try:
        row = await provision_device(session, tenant_id=tenant_id, device_id=device_id,
                                     client=_client(device), receiver_host=s.syslog_receiver_host,
                                     receiver_port=s.syslog_tls_port)
    except OpnsenseError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=type(exc).__name__) from exc
    await AuditService(session).record(
        actor_user_id=ctx.user.id, tenant_id=tenant_id, action="log_forwarding.enable",
        target_type="device", target_id=str(device_id),
        ip=request.client.host if request.client else None, details={"serial": row.cert_serial})
    out = _out(row, device_id=device_id)
    await session.commit()
    return out


@router.post("/disable", response_model=LogForwardingOut, dependencies=[Depends(enforce_csrf)])
async def disable_log_forwarding(
    tenant_id: uuid.UUID, device_id: uuid.UUID, request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.CONFIG_PUSH)),
    session: AsyncSession = Depends(get_session),
) -> LogForwardingOut:
    device = await _device(session, tenant_id, device_id)
    try:
        await deprovision_device(session, device_id=device_id, client=_client(device))
    except OpnsenseError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=type(exc).__name__) from exc
    await AuditService(session).record(
        actor_user_id=ctx.user.id, tenant_id=tenant_id, action="log_forwarding.disable",
        target_type="device", target_id=str(device_id),
        ip=request.client.host if request.client else None, details={})
    out = _out(await DeviceLogForwardingRepository(session, tenant_id).get(device_id), device_id=device_id)
    await session.commit()
    return out


@router.post("/rotate", response_model=LogForwardingOut, dependencies=[Depends(enforce_csrf)])
async def rotate_log_forwarding(
    tenant_id: uuid.UUID, device_id: uuid.UUID, request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.CONFIG_PUSH)),
    session: AsyncSession = Depends(get_session),
) -> LogForwardingOut:
    device = await _device(session, tenant_id, device_id)
    s = get_settings()
    try:
        row = await rotate_device_cert(session, tenant_id=tenant_id, device_id=device_id,
                                       client=_client(device), receiver_host=s.syslog_receiver_host,
                                       receiver_port=s.syslog_tls_port)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OpnsenseError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=type(exc).__name__) from exc
    await AuditService(session).record(
        actor_user_id=ctx.user.id, tenant_id=tenant_id, action="log_forwarding.rotate",
        target_type="device", target_id=str(device_id),
        ip=request.client.host if request.client else None, details={"serial": row.cert_serial})
    out = _out(row, device_id=device_id)
    await session.commit()
    return out


@router.post("/revoke", response_model=LogForwardingOut, dependencies=[Depends(enforce_csrf)])
async def revoke_log_forwarding(
    tenant_id: uuid.UUID, device_id: uuid.UUID, request: Request, body: RevokeIn,
    ctx: TenantContext = Depends(require_tenant(Action.CONFIG_PUSH)),
    session: AsyncSession = Depends(get_session),
) -> LogForwardingOut:
    device = await _device(session, tenant_id, device_id)
    try:
        row = await revoke_device(session, tenant_id=tenant_id, device_id=device_id,
                                  client=_client(device), reason=body.reason)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OpnsenseError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=type(exc).__name__) from exc
    await AuditService(session).record(
        actor_user_id=ctx.user.id, tenant_id=tenant_id, action="log_forwarding.revoke",
        target_type="device", target_id=str(device_id),
        ip=request.client.host if request.client else None,
        details={"serial": row.cert_serial, "reason": body.reason})
    out = _out(row, device_id=device_id)
    await session.commit()
    return out
