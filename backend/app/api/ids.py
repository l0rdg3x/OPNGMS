import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense.client import OpnsenseError
from app.core.db import get_session
from app.core.deps import TenantContext, require_tenant
from app.core.rbac import Action
from app.models.device import Device
from app.services.device_client import client_for_device

router = APIRouter(prefix="/api", tags=["ids"])


@router.get("/tenants/{tenant_id}/devices/{device_id}/opnsense/ids/rulesets")
async def list_ids_rulesets(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """The device's installed IDS rulesets, trimmed to {filename, description, enabled} for the form."""
    device = await session.get(Device, device_id)
    if device is None or device.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    client = client_for_device(device)
    try:
        rows = await client.list_ids_rulesets()
    except OpnsenseError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=type(exc).__name__) from exc
    return [
        {"filename": r.get("filename"), "description": r.get("description"), "enabled": r.get("enabled")}
        for r in rows
    ]
