import gzip
import uuid

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense.client import OpnsenseClient, OpnsenseError
from app.core import crypto
from app.core.db import get_session
from app.core.deps import TenantContext, require_tenant
from app.core.rbac import Action
from app.models.config_snapshot import ConfigSnapshot
from app.models.device import Device
from app.repositories.config_snapshot import ConfigSnapshotRepository
from app.schemas.config import (
    CapabilityInventory,
    ConfigDiffEntry,
    ConfigSnapshotOut,
    DriftSummary,
)
from app.services.capability import build_inventory
from app.services.config_diff import structural_diff
from app.services.config_model import build_tree

router = APIRouter(prefix="/api/tenants/{tenant_id}", tags=["config"])


def _xml(snapshot: ConfigSnapshot) -> str:
    """Decrypt + decompress a snapshot's content server-side (never exposed to clients)."""
    return gzip.decompress(crypto.decrypt_bytes(snapshot.content_enc)).decode("utf-8")


@router.get(
    "/devices/{device_id}/config/snapshots",
    response_model=list[ConfigSnapshotOut],
)
async def list_snapshots(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> list[ConfigSnapshotOut]:
    rows = await ConfigSnapshotRepository(session, tenant_id).list(device_id)
    return [ConfigSnapshotOut.model_validate(r) for r in rows]


@router.get("/devices/{device_id}/config/drift", response_model=DriftSummary)
async def config_drift(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> DriftSummary:
    rows = await ConfigSnapshotRepository(session, tenant_id).list(device_id)
    return DriftSummary(
        version_count=len(rows),
        latest_taken_at=rows[0].taken_at if rows else None,
        changed_since_previous=len(rows) >= 2,
    )


@router.get(
    "/devices/{device_id}/config/diff",
    response_model=list[ConfigDiffEntry],
)
async def config_diff(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    from_id: uuid.UUID = Query(..., alias="from"),
    to_id: uuid.UUID = Query(..., alias="to"),
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> list[ConfigDiffEntry]:
    repo = ConfigSnapshotRepository(session, tenant_id)
    a = await repo.get(from_id)
    b = await repo.get(to_id)
    if a is None or b is None or a.device_id != device_id or b.device_id != device_id:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    # Decrypt both server-side, return the per-path structural diff (paths only, NO values).
    return [ConfigDiffEntry(**c) for c in structural_diff(_xml(a), _xml(b))]


@router.get("/devices/{device_id}/config/model", response_model=dict)
async def config_model(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Schema-agnostic navigable tree of the device's latest config.

    Decrypted + parsed server-side; sensitive leaf values are redacted by build_tree
    (sensitive=True, value=None) and never emitted in the response.
    """
    snap = await ConfigSnapshotRepository(session, tenant_id).latest(device_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="No config snapshot for device")
    return build_tree(_xml(snap))


@router.get(
    "/devices/{device_id}/config/capabilities",
    response_model=CapabilityInventory,
)
async def config_capabilities(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> CapabilityInventory:
    """Per-device capability inventory: empirical (from the latest config) + live probe.

    Builds an OpnsenseClient and probes installed plugins/version; on ANY connector or
    credential error it degrades gracefully to empirical-only (no available_capabilities),
    so the endpoint stays useful even when the device is unreachable.
    """
    repo = ConfigSnapshotRepository(session, tenant_id)
    snap = await repo.latest(device_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="No config snapshot for device")
    # Live probe; degrade gracefully to empirical-only on any connector/credential error.
    plugin_info: dict = {"plugins": []}
    device = await session.get(Device, device_id)
    if device is not None:
        try:
            client = OpnsenseClient(
                device.base_url,
                crypto.decrypt(device.api_key_enc),
                crypto.decrypt(device.api_secret_enc),
                verify_tls=device.verify_tls,
            )
            plugin_info = await client.get_plugin_info()
        except (OpnsenseError, InvalidToken):
            plugin_info = {"plugins": []}
    inv = build_inventory(_xml(snap), snap.opnsense_version, plugin_info)
    return CapabilityInventory(**inv)
