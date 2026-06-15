import uuid

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense.client import OpnsenseClient, OpnsenseError
from app.core import crypto
from app.core.db import get_session
from app.core.deps import TenantContext, enforce_csrf, require_tenant
from app.core.rbac import Action
from app.models.config_change import ConfigChange
from app.models.device import Device
from app.schemas.catalog import CatalogChangeIn, PluginModelOut
from app.schemas.config import ConfigChangeOut
from app.services import catalog_provider, catalog_versions
from app.services.audit import AuditService
from app.services.catalog_kind import CATALOG_DENYLIST
from app.services.catalog_live import (
    extract_grid_options,
    extract_grid_rows,
    extract_options,
    flatten_values,
)
from app.services.config_push import create_change

router = APIRouter(prefix="/api/tenants/{tenant_id}", tags=["catalog"])


async def _load_device(session: AsyncSession, tenant_id: uuid.UUID, device_id: uuid.UUID) -> Device:
    device = await session.get(Device, device_id)
    if device is None or device.tenant_id != tenant_id:  # explicit ownership guard (defence-in-depth vs RLS)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return device


async def _catalog_model(session: AsyncSession, device: Device, model_id: str) -> dict | None:
    """A model's schema from the device's core catalog, falling back to its plugins catalog."""
    core = await catalog_provider.get_catalog(session, device.edition, device.firmware_version or "")
    model = (core or {}).get("models", {}).get(model_id)
    if model is not None:
        return model
    plugins = await catalog_provider.get_plugins_catalog(
        session, device.edition, device.firmware_version or "")
    return (plugins or {}).get("models", {}).get(model_id)


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
        "xml_path": model.get("xml_path", ""),  # the model's config.xml subtree (for revert reconstruction)
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
    if body.model_id in CATALOG_DENYLIST:
        raise HTTPException(status_code=422, detail=f"model {body.model_id!r} is not editable (safety denylist)")
    model = await _catalog_model(session, device, body.model_id)
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
        "menu": catalog.get("menu", []),  # the OPNsense-like nav tree (3b); [] for pre-3b catalogs
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
    model = await _catalog_model(session, device, model_id)
    if model is None:
        raise HTTPException(status_code=404, detail=f"unknown model: {model_id!r}")
    base = {"model": model, "values": {}, "grids": {}, "field_options": {}, "grid_field_options": {},
            "reachable": False, "read_only": model_id in CATALOG_DENYLIST}
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
    base["field_options"] = extract_options(raw, model)
    base["grid_field_options"] = {
        g["path"]: extract_grid_options(raw, model, g) for g in model.get("grids", [])}
    return base


@router.get("/devices/{device_id}/plugin-models", response_model=list[PluginModelOut])
async def read_plugin_models(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Plugins that have an editable config model: [{package, model_id, title}] (for the Configure link)."""
    device = await _load_device(session, tenant_id, device_id)
    plugins = await catalog_provider.get_plugins_catalog(
        session, device.edition, device.firmware_version or "")
    out: list[dict] = []
    for model_id, m in (plugins or {}).get("models", {}).items():
        pl = m.get("plugin") or {}
        if pl.get("package"):
            out.append({"package": pl["package"], "model_id": model_id, "title": pl.get("title", "")})
    return out


@router.get("/devices/{device_id}/catalog/diff")
async def read_catalog_diff(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    from_version: str | None = Query(default=None, alias="from"),
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Cross-version catalog diff: the device's catalog vs a baseline (default the previous published)."""
    device = await _load_device(session, tenant_id, device_id)
    to_catalog = await catalog_provider.get_catalog(session, device.edition, device.firmware_version or "")
    if to_catalog is None:
        raise HTTPException(status_code=404, detail="No catalog available for this device version")
    dev_ver = to_catalog.get("version", "")
    versions = await catalog_provider.published_versions(device.edition or "community")
    baselines = [v for v in versions if catalog_provider._parse_version(v)
                 < catalog_provider._parse_version(dev_ver)]
    chosen = from_version or catalog_provider.previous_version(versions, dev_ver)
    empty = {"added_models": [], "removed_models": [], "models": {}}
    if not chosen or chosen == dev_ver:
        return {"from": None, "to": dev_ver, "available_baselines": baselines, "diff": empty}
    from_catalog = await catalog_provider.get_catalog(session, device.edition, chosen)
    if from_catalog is None:
        return {"from": None, "to": dev_ver, "available_baselines": baselines, "diff": empty}
    return {
        "from": chosen, "to": dev_ver, "available_baselines": baselines,
        "diff": catalog_versions.diff_catalogs(from_catalog, to_catalog),
    }
