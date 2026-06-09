import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session, set_tenant_context
from app.core.rbac import Action, can
from app.models.membership import Membership
from app.models.tenant import Tenant
from app.models.user import User
from app.services.auth import AuthService

SESSION_COOKIE = "opngms_session"
CSRF_HEADER = "X-OPNGMS-CSRF"


async def enforce_csrf(request: Request) -> None:
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if not request.headers.get(CSRF_HEADER):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing CSRF header",
            )


async def get_current_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> User:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        session_id = uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    user = await AuthService(session).get_user_for_session(session_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    return user


@dataclass
class TenantContext:
    tenant: Tenant
    user: User
    role: str | None  # None for superadmin without a membership


async def tenant_context(
    tenant_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TenantContext:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    role: str | None = None
    if not user.is_superadmin:
        result = await session.execute(
            select(Membership).where(
                Membership.user_id == user.id, Membership.tenant_id == tenant_id
            )
        )
        membership = result.scalar_one_or_none()
        if membership is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Tenant access denied"
            )
        role = membership.role
    # RLS wiring: set app.current_tenant for this transaction.
    await set_tenant_context(session, tenant_id)
    return TenantContext(tenant=tenant, user=user, role=role)


def require_tenant(action: Action):
    async def _dep(ctx: TenantContext = Depends(tenant_context)) -> TenantContext:
        if not can(is_superadmin=ctx.user.is_superadmin, role=ctx.role, action=action):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied"
            )
        return ctx

    return _dep


def require_org(action: Action):
    async def _dep(user: User = Depends(get_current_user)) -> User:
        if not can(is_superadmin=user.is_superadmin, role=None, action=action):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied"
            )
        return user

    return _dep
