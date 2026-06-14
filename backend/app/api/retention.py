import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import TenantContext, enforce_csrf, require_tenant
from app.core.rbac import Action
from app.repositories.tenant_retention import TenantRetentionRepository
from app.schemas.retention import RetentionOut, RetentionPatch, RetentionWarning
from app.services.audit import AuditService
from app.services.report_retention import schedule_retention_warnings
from app.services.retention import RETENTION_STORES
from app.services.runtime_settings import get_runtime_config

router = APIRouter(prefix="/api/tenants/{tenant_id}", tags=["retention"])


async def _defaults(session: AsyncSession) -> dict[str, int]:
    cfg = await get_runtime_config(session)
    return {s: int(cfg[f"{s}_retention_days"]) for s in RETENTION_STORES}


@router.get("/retention", response_model=RetentionOut)
async def get_retention(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> RetentionOut:
    overrides = await TenantRetentionRepository(session, tenant_id).get_overrides()
    warnings = [RetentionWarning(**w) for w in await schedule_retention_warnings(session, tenant_id)]
    return RetentionOut(overrides=overrides, defaults=await _defaults(session), warnings=warnings)


@router.put("/retention", response_model=RetentionOut, dependencies=[Depends(enforce_csrf)])
async def put_retention(
    tenant_id: uuid.UUID, body: RetentionPatch, request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.RETENTION_CONFIG)),
    session: AsyncSession = Depends(get_session),
) -> RetentionOut:
    unknown = sorted(k for k in body.values if k not in RETENTION_STORES)
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown store(s): {', '.join(unknown)}")
    merged = await TenantRetentionRepository(session, tenant_id).upsert(dict(body.values))
    await AuditService(session).record(
        actor_user_id=ctx.user.id, tenant_id=tenant_id, action="tenant.retention.update",
        target_type="tenant_retention", target_id=str(tenant_id),
        ip=request.client.host if request.client else None, details={"patch": dict(body.values)},
    )
    # Read the defaults before committing (the session's state after commit is intentionally not relied on).
    defaults = await _defaults(session)
    await session.commit()
    return RetentionOut(overrides=merged, defaults=defaults)
