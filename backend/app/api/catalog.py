import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import TenantContext, enforce_csrf, require_tenant
from app.core.rbac import Action
from app.models.config_change import ConfigChange
from app.models.device import Device
from app.schemas.catalog import CatalogChangeIn
from app.schemas.config import ConfigChangeOut
from app.services import catalog_provider
from app.services.audit import AuditService
from app.services.catalog_kind import CATALOG_DENYLIST
from app.services.config_push import create_change

router = APIRouter(prefix="/api/tenants/{tenant_id}", tags=["catalog"])


async def _load_device(session: AsyncSession, tenant_id: uuid.UUID, device_id: uuid.UUID) -> Device:
    device = await session.get(Device, device_id)
    if device is None or device.tenant_id != tenant_id:  # explicit ownership guard (defence-in-depth vs RLS)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return device


def _build_payload(model: dict, body: CatalogChangeIn) -> dict:
    """Validate scalars/grids against the catalog model and embed the resolved endpoints.

    Raises HTTPException(422) on any unknown field/grid or malformed grid op.
    """
    field_paths = {f["path"] for f in model.get("fields", [])}
    unknown = set(body.scalars) - field_paths
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown scalar field(s): {sorted(unknown)}")
    grids_by_path = {g["path"]: g for g in model.get("grids", [])}
    grids_payload = []
    for opp in body.grids:
        gdef = grids_by_path.get(opp.grid)
        if gdef is None:
            raise HTTPException(status_code=422, detail=f"unknown grid: {opp.grid!r}")
        if opp.op in ("add", "set") and opp.item is None:
            raise HTTPException(status_code=422, detail=f"grid op {opp.op} requires 'item'")
        if opp.op in ("set", "del") and not opp.uuid:
            raise HTTPException(status_code=422, detail=f"grid op {opp.op} requires 'uuid'")
        grids_payload.append({
            "op": opp.op, "endpoints": gdef.get("endpoints", {}),
            "row": opp.grid.split(".")[-1], "uuid": opp.uuid, "item": opp.item})
    eps = model.get("endpoints", {})
    return {
        "model_id": model["id"], "set_path": eps.get("set", ""),
        "reconfigure_path": eps.get("reconfigure", ""), "model_root": model.get("model_root", ""),
        "scalars": dict(body.scalars), "grids": grids_payload,
    }


@router.post(
    "/devices/{device_id}/catalog/changes",
    response_model=ConfigChangeOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_csrf)],
)
async def create_catalog_change(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    body: CatalogChangeIn,
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.CONFIG_PUSH)),
    session: AsyncSession = Depends(get_session),
) -> ConfigChange:
    device = await _load_device(session, tenant_id, device_id)
    catalog = await catalog_provider.get_catalog(session, device.edition, device.firmware_version or "")
    if catalog is None:
        raise HTTPException(status_code=404, detail="No catalog available for this device version")
    if body.model_id in CATALOG_DENYLIST:
        raise HTTPException(status_code=422, detail=f"model {body.model_id!r} is not editable (safety denylist)")
    model = catalog.get("models", {}).get(body.model_id)
    if model is None:
        raise HTTPException(status_code=422, detail=f"unknown model: {body.model_id!r}")
    payload = _build_payload(model, body)
    change = await create_change(
        session, tenant_id=tenant_id, device_id=device_id, created_by=ctx.user.id,
        kind="catalog_setting", operation="set", target=body.model_id, payload=payload)
    await AuditService(session).record(
        actor_user_id=ctx.user.id, tenant_id=tenant_id, action="config.catalog.create",
        target_type="config_change", target_id=str(change.id),
        ip=request.client.host if request.client else None,
        details={"model_id": body.model_id})
    await session.commit()
    return change
