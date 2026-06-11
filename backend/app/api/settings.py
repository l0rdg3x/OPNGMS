import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense.client import OpnsenseClient, OpnsenseError
from app.connectors.opnsense.setting_endpoints import SETTING_ENDPOINTS
from app.core import crypto
from app.core.db import get_session
from app.core.deps import TenantContext, get_current_user, require_tenant
from app.core.rbac import Action
from app.models.device import Device
from app.models.user import User
from app.services.setting_introspect import infer_fields

router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/opnsense/setting-endpoints")
async def list_setting_endpoints(user: User = Depends(get_current_user)) -> list[dict]:
    """The curated catalog of fleet-portable setting endpoints (powers the kind picker)."""
    return [{"key": e.key, "label": e.label} for e in SETTING_ENDPOINTS.values()]


@router.get("/tenants/{tenant_id}/devices/{device_id}/opnsense/settings/{endpoint_key}")
async def introspect_setting(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    endpoint_key: str,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Read the device's setting `get` and return a value-controlled field schema for the form."""
    ep = SETTING_ENDPOINTS.get(endpoint_key)
    if ep is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown setting endpoint")
    device = await session.get(Device, device_id)
    if device is None or device.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    client = OpnsenseClient(
        device.base_url,
        crypto.decrypt(device.api_key_enc),
        crypto.decrypt(device.api_secret_enc),
        verify_tls=device.verify_tls,
        tls_fingerprint=device.tls_fingerprint,
    )
    try:
        raw = await client.get_setting(ep.get_path)
    except OpnsenseError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=type(exc).__name__) from exc
    return {"endpoint_key": ep.key, "label": ep.label, "fields": infer_fields(raw, ep)}
