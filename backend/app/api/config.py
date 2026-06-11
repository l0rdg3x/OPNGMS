import gzip
import uuid

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense.client import OpnsenseClient, OpnsenseError
from app.core import crypto
from app.core.db import get_session
from app.core.deps import TenantContext, enforce_csrf, require_tenant
from app.core.queue import get_enqueuer
from app.core.rbac import Action
from app.models.config_change import ConfigChange
from app.models.config_snapshot import ConfigSnapshot
from app.models.device import Device
from app.repositories.config_change import ConfigChangeRepository
from app.repositories.config_snapshot import ConfigSnapshotRepository
from app.schemas.config import (
    CapabilityInventory,
    ConfigChangeIn,
    ConfigChangeOut,
    ConfigDiffEntry,
    ConfigSnapshotOut,
    DriftSummary,
    ScheduleIn,
)
from app.services.audit import AuditService
from app.services.capability import build_inventory
from app.services.config_diff import structural_diff
from app.services.config_model import build_tree
from app.services.config_push import create_change, preview_change

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
                tls_fingerprint=device.tls_fingerprint,
            )
            plugin_info = await client.get_plugin_info()
        except (OpnsenseError, InvalidToken):
            plugin_info = {"plugins": []}
    inv = build_inventory(_xml(snap), snap.opnsense_version, plugin_info, edition=device.edition if device is not None else "")
    return CapabilityInventory(**inv)


# --- Config change & push pipeline (4D-a) ---------------------------------
#
# create/schedule/cancel mutate state and are gated by CONFIG_PUSH (the elevated
# mutation action) + CSRF; list/preview are read-only and gated by DEVICE_VIEW.
# ConfigChangeOut deliberately hides payload/result/baseline_hash (internal).


@router.post(
    "/devices/{device_id}/config/changes",
    response_model=ConfigChangeOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_csrf)],
)
async def create_config_change(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    payload: ConfigChangeIn,
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.CONFIG_PUSH)),
    session: AsyncSession = Depends(get_session),
) -> ConfigChange:
    # Cross-tenant guard: the worker applies changes as the DB owner (RLS bypassed)
    # and loads the device by id, so a change must never be created for a device the
    # caller cannot see. Under RLS this lookup returns None for another tenant's device.
    device = await session.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    change = await create_change(
        session,
        tenant_id=tenant_id,
        device_id=device_id,
        created_by=ctx.user.id,
        kind=payload.kind,
        operation=payload.operation,
        target=payload.target,
        payload=payload.payload,
    )
    await AuditService(session).record(
        actor_user_id=ctx.user.id,
        tenant_id=tenant_id,
        action="config.change.create",
        target_type="config_change",
        target_id=str(change.id),
        ip=request.client.host if request.client else None,
        details={"kind": change.kind, "op": change.operation},
    )
    await session.commit()
    return change


@router.get(
    "/devices/{device_id}/config/changes",
    response_model=list[ConfigChangeOut],
)
async def list_config_changes(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> list[ConfigChange]:
    return list(await ConfigChangeRepository(session, tenant_id).list(device_id))


@router.get("/devices/{device_id}/config/changes/{change_id}/preview")
async def preview_config_change(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    change_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    change = await ConfigChangeRepository(session, tenant_id).get(change_id)
    if change is None or change.device_id != device_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Change not found")
    return preview_change(change)


@router.post(
    "/devices/{device_id}/config/changes/{change_id}/schedule",
    response_model=ConfigChangeOut,
    dependencies=[Depends(enforce_csrf)],
)
async def schedule_config_change(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    change_id: uuid.UUID,
    body: ScheduleIn,
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.CONFIG_PUSH)),
    session: AsyncSession = Depends(get_session),
    enqueue=Depends(get_enqueuer),
) -> ConfigChange:
    repo = ConfigChangeRepository(session, tenant_id)
    change = await repo.get(change_id)
    if change is None or change.device_id != device_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Change not found")
    if change.status not in ("draft", "scheduled"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot schedule a change in status {change.status}",
        )
    change.status = "scheduled"
    change.scheduled_at = body.scheduled_at
    await session.flush()
    await AuditService(session).record(
        actor_user_id=ctx.user.id,
        tenant_id=tenant_id,
        action="config.change.schedule",
        target_type="config_change",
        target_id=str(change.id),
        ip=request.client.host if request.client else None,
        details={
            "scheduled_at": body.scheduled_at.isoformat()
            if body.scheduled_at
            else "immediate"
        },
    )
    await session.commit()
    # Immediate -> defer_until=None; deferred -> defer_until=scheduled_at.
    await enqueue("apply_config_change", str(change.id), defer_until=body.scheduled_at)
    return change


@router.post(
    "/devices/{device_id}/config/changes/{change_id}/cancel",
    response_model=ConfigChangeOut,
    dependencies=[Depends(enforce_csrf)],
)
async def cancel_config_change(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    change_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.CONFIG_PUSH)),
    session: AsyncSession = Depends(get_session),
) -> ConfigChange:
    repo = ConfigChangeRepository(session, tenant_id)
    change = await repo.get(change_id)
    if change is None or change.device_id != device_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Change not found")
    if change.status not in ("draft", "scheduled"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel a change in status {change.status}",
        )
    change.status = "cancelled"
    await session.flush()
    await AuditService(session).record(
        actor_user_id=ctx.user.id,
        tenant_id=tenant_id,
        action="config.change.cancel",
        target_type="config_change",
        target_id=str(change.id),
        ip=request.client.host if request.client else None,
        details={},
    )
    await session.commit()
    return change
