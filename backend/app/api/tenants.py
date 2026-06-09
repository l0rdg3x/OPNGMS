from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import enforce_csrf, require_org
from app.core.rbac import Action
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.tenant import TenantRepository
from app.schemas.tenant import TenantIn, TenantOut
from app.services.audit import AuditService

router = APIRouter(prefix="/api/tenants", tags=["tenants"])


@router.get("", response_model=list[TenantOut])
async def list_tenants(
    user: User = Depends(require_org(Action.TENANT_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> list[Tenant]:
    return await TenantRepository(session).list()


@router.post(
    "",
    response_model=TenantOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_csrf)],
)
async def create_tenant(
    payload: TenantIn,
    request: Request,
    user: User = Depends(require_org(Action.TENANT_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> Tenant:
    repo = TenantRepository(session)
    tenant = await repo.add(Tenant(name=payload.name, slug=payload.slug, note=payload.note))
    await AuditService(session).record(
        actor_user_id=user.id,
        tenant_id=tenant.id,
        action="tenant.create",
        target_type="tenant",
        target_id=str(tenant.id),
        ip=request.client.host if request.client else None,
        details={"slug": tenant.slug},
    )
    await session.commit()
    return tenant
