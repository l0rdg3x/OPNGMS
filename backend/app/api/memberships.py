import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import TenantContext, enforce_csrf, require_tenant
from app.core.rbac import Action
from app.models.membership import Membership
from app.repositories.membership import MembershipRepository
from app.schemas.membership import MembershipIn, MembershipOut
from app.services.audit import AuditService

router = APIRouter(prefix="/api/tenants/{tenant_id}/memberships", tags=["memberships"])


@router.get("", response_model=list[MembershipOut])
async def list_memberships(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.MEMBERSHIP_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> list[Membership]:
    return await MembershipRepository(session).list_for_tenant(tenant_id)


@router.post(
    "",
    response_model=MembershipOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_csrf)],
)
async def create_membership(
    tenant_id: uuid.UUID,
    payload: MembershipIn,
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.MEMBERSHIP_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> Membership:
    repo = MembershipRepository(session)
    membership = await repo.add(
        Membership(user_id=payload.user_id, tenant_id=tenant_id, role=payload.role)
    )
    await AuditService(session).record(
        actor_user_id=ctx.user.id,
        tenant_id=tenant_id,
        action="membership.create",
        target_type="membership",
        target_id=str(membership.id),
        ip=request.client.host if request.client else None,
        details={"user_id": str(payload.user_id), "role": payload.role},
    )
    await session.commit()
    return membership
