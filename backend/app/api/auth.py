import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import SESSION_COOKIE, enforce_csrf, get_current_user
from app.core.ratelimit import SlidingWindowLimiter
from app.models.user import User
from app.schemas.auth import LoginIn, MeOut
from app.services.audit import AuditService
from app.services.auth import AuthService

router = APIRouter(prefix="/api", tags=["auth"])

_s = get_settings()
login_limiter = SlidingWindowLimiter(_s.login_max_attempts, _s.login_lockout_window_seconds)


@router.post("/login", response_model=MeOut)
async def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> User:
    ip = request.client.host if request.client else "?"
    key = f"{payload.email.lower()}|{ip}"

    allowed, retry = login_limiter.check(key)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many attempts",
            headers={"Retry-After": str(retry)},
        )

    svc = AuthService(session)
    user = await svc.authenticate(payload.email, payload.password)
    if user is None:
        login_limiter.record_failure(key)
        await AuditService(session).record(
            actor_user_id=None,
            tenant_id=None,
            action="auth.login.failed",
            target_type="auth",
            target_id=None,
            ip=ip,
            details={"email": payload.email},
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )

    settings = get_settings()
    sess = await svc.create_session(user, settings.session_ttl_hours)
    login_limiter.reset(key)
    await AuditService(session).record(
        actor_user_id=user.id,
        tenant_id=None,
        action="auth.login",
        target_type="session",
        target_id=str(sess.id),
        ip=ip,
        details={},
    )
    await session.commit()
    response.set_cookie(
        SESSION_COOKIE,
        str(sess.id),
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=settings.session_ttl_hours * 3600,
    )
    return user


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(enforce_csrf)],
)
async def logout(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        try:
            await AuthService(session).delete_session(uuid.UUID(raw))
            await AuditService(session).record(
                actor_user_id=user.id,
                tenant_id=None,
                action="auth.logout",
                target_type="session",
                target_id=raw,
                ip=request.client.host if request.client else None,
                details={},
            )
            await session.commit()
        except ValueError:
            pass
    response.delete_cookie(SESSION_COOKIE)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=MeOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user
