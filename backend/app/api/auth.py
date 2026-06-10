import logging
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

logger = logging.getLogger(__name__)

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

    # Fail CLOSED on a limiter fault: this gates credential validation, so a transient limiter error
    # must NOT silently disable brute-force protection. Brief 503 unavailability is the safer failure
    # mode; the limiter is in-process + memory-bounded, so a fault here is a genuine (rare) defect we
    # want to surface. Always log it so operators can detect a degraded defense.
    try:
        allowed, retry = login_limiter.check(key)
    except Exception:  # noqa: BLE001
        logger.error("login rate-limiter check failed; failing closed", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Login temporarily unavailable",
            headers={"Retry-After": "5"},
        )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many attempts",
            headers={"Retry-After": str(retry)},
        )

    svc = AuthService(session)
    user = await svc.authenticate(payload.email, payload.password)
    if user is None:
        try:
            login_limiter.record_failure(key)
        except Exception:  # noqa: BLE001 — never let a limiter fault turn a 401 into a 500
            logger.error("login rate-limiter record_failure failed", exc_info=True)
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
    try:
        login_limiter.reset(key)
    except Exception:  # noqa: BLE001 — never let a limiter fault break a successful login
        logger.error("login rate-limiter reset failed", exc_info=True)
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
