import uuid

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense.client import OpnsenseClient, OpnsenseError
from app.core import crypto
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
from app.services.catalog_live import extract_grid_rows, flatten_values
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
    # The applier reloads the service ONCE after all scalar/grid writes. A model with no reconfigure
    # endpoint would mutate the device then fail the reload (partial apply) — refuse at proposal time.
    if not eps.get("reconfigure"):
        raise HTTPException(status_code=422, detail="model has no reconfigure endpoint (cannot apply safely)")
    return {
        "model_id": model["id"], "set_path": eps.get("set", ""),
        "reconfigure_path": eps["reconfigure"], "model_root": model.get("model_root", ""),
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


@router.get("/devices/{device_id}/catalog")
async def read_device_catalog(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """The device's catalog (schema only), denylist-flagged. Live values come at edit time (sub-3).

    For a Business device, `resolved_*` is the Community base actually served (the shared core)."""
    device = await _load_device(session, tenant_id, device_id)
    catalog = await catalog_provider.get_catalog(session, device.edition, device.firmware_version or "")
    if catalog is None:
        raise HTTPException(status_code=404, detail="No catalog available for this device version")
    models = {
        mid: {**m, "read_only": mid in CATALOG_DENYLIST}
        for mid, m in catalog.get("models", {}).items()
    }
    return {
        "edition": device.edition or "community",
        "version": device.firmware_version or "",
        "resolved_edition": catalog.get("edition", ""),
        "resolved_version": catalog.get("version", ""),
        "models": models,
    }


@router.get("/devices/{device_id}/catalog/models/{model_id}")
async def read_catalog_model(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    model_id: str,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """A catalog model's schema + the device's LIVE current values (for the editor form).

    Reads `<model>/settings/get` live; degrades to reachable:false on any connector/credential error.
    Denylisted models are returned read_only with no live read."""
    device = await _load_device(session, tenant_id, device_id)
    catalog = await catalog_provider.get_catalog(session, device.edition, device.firmware_version or "")
    if catalog is None:
        raise HTTPException(status_code=404, detail="No catalog available for this device version")
    model = catalog.get("models", {}).get(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail=f"unknown model: {model_id!r}")
    base = {"model": model, "values": {}, "grids": {}, "reachable": False,
            "read_only": model_id in CATALOG_DENYLIST}
    if base["read_only"]:
        return base
    try:
        client = OpnsenseClient(
            device.base_url, crypto.decrypt(device.api_key_enc), crypto.decrypt(device.api_secret_enc),
            verify_tls=device.verify_tls, tls_fingerprint=device.tls_fingerprint,
            edition=device.edition, version=device.firmware_version or "")
        raw = await client.get_setting(model["endpoints"]["get"])
    except (OpnsenseError, InvalidToken, ValueError, KeyError):
        return base  # unreachable / unreadable -> schema only, editing disabled
    base["reachable"] = True
    base["values"] = flatten_values(raw, model)
    base["grids"] = {g["path"]: extract_grid_rows(raw, model, g) for g in model.get("grids", [])}
    return base
