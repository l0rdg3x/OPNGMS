import secrets
import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session, set_tenant_context
from app.core.rbac import Action, can
from app.models.membership import Membership
from app.models.session import Session
from app.models.tenant import Tenant
from app.models.user import User
from app.services.auth import AuthService

SESSION_COOKIE = "opngms_session"
CSRF_COOKIE = "opngms_csrf"  # readable (non-httponly) cookie carrying the per-session CSRF token
CSRF_HEADER = "X-OPNGMS-CSRF"


async def get_current_session(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Session:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    sess = await AuthService(session).get_session_for_token(raw)
    if sess is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    return sess


async def enforce_csrf(
    request: Request, sess: Session = Depends(get_current_session)
) -> None:
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        header = request.headers.get(CSRF_HEADER)
        if not header or not secrets.compare_digest(header, sess.csrf_token):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="CSRF check failed"
            )


async def get_current_user(
    sess: Session = Depends(get_current_session),
    session: AsyncSession = Depends(get_session),
) -> User:
    # MFA-pending / MFA-setup sessions cannot reach normal app endpoints (fail-closed).
    if sess.kind != "full":
        detail = "mfa_setup_required" if sess.kind == "mfa_setup" else "mfa_required"
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    user = await AuthService(session).get_user_for_session(sess)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    return user


async def get_enrollment_ctx(
    sess: Session = Depends(get_current_session),
    session: AsyncSession = Depends(get_session),
) -> tuple[User, Session]:
    """User for an endpoint reachable in MFA-setup mode (kind full or mfa_setup, NOT mfa_pending)."""
    if sess.kind not in ("full", "mfa_setup"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user = await AuthService(session).get_user_for_session(sess)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    return user, sess


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
