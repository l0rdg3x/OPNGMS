import gzip
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.db import get_session
from app.core.deps import TenantContext, require_tenant
from app.core.rbac import Action
from app.models.config_snapshot import ConfigSnapshot
from app.repositories.config_snapshot import ConfigSnapshotRepository
from app.schemas.config import ConfigDiffEntry, ConfigSnapshotOut, DriftSummary
from app.services.config_diff import structural_diff

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
