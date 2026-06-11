import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    enforce_csrf,
    get_current_session,
    get_current_user,
    get_enrollment_ctx,
)
from app.core.ratelimit import SlidingWindowLimiter
from app.models.session import Session
from app.models.user import User
from app.models.user_mfa import UserMfa
from app.models.user_recovery_code import UserRecoveryCode
from app.schemas.auth import LoginIn, LoginOut, MeOut, SessionInfo
from app.schemas.mfa import CodeIn
from app.services import mfa as mfa_svc
from app.services.app_settings import get_mfa_policy
from app.services.audit import AuditService
from app.services.auth import AuthService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["auth"])

_s = get_settings()
login_limiter = SlidingWindowLimiter(_s.login_max_attempts, _s.login_lockout_window_seconds)


def _client_ip(request: Request) -> str | None:
    # Behind a reverse proxy, uvicorn's --proxy-headers + --forwarded-allow-ips resolves the real
    # client from the trusted X-Forwarded-For chain into request.client.host. We read that rather
    # than parsing X-Forwarded-For ourselves: doing it in-app would bypass uvicorn's trust boundary
    # and let any client spoof the header. See docker-compose.prod.yml (api command) and nginx.conf.
    return request.client.host if request.client else None


@router.post("/login", response_model=LoginOut)
async def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> LoginOut:
    client_ip = _client_ip(request)
    ip = client_ip or "?"
    key = f"{payload.email.lower()}|{ip}"

    # Fail CLOSED on a limiter fault: this gates credential validation, so a transient limiter error
    # must NOT silently disable brute-force protection. Brief 503 unavailability is the safer failure
    # mode; the limiter is in-process + memory-bounded, so a fault here is a genuine (rare) defect we
    # want to surface. Always log it so operators can detect a degraded defense.
    try:
        allowed, retry = login_limiter.check(key)
    except Exception as exc:  # noqa: BLE001
        logger.error("login rate-limiter check failed; failing closed", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Login temporarily unavailable",
            headers={"Retry-After": "5"},
        ) from exc
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
    # Anti-fixation rotation: drop any session presented in the incoming cookie.
    old = request.cookies.get(SESSION_COOKIE)
    if old:
        await svc.delete_session_by_token(old)

    # Decide the session kind: enrolled -> challenge (mfa_pending); policy requires but not
    # enrolled -> setup-only (mfa_setup); otherwise a normal full session.
    mfa_row = await session.get(UserMfa, user.id)
    policy = await get_mfa_policy(session)
    is_priv = user.is_superadmin  # privileged = superadmin (tenant_admin membership is a later refinement)
    if mfa_row and mfa_row.enabled:
        kind = "mfa_pending"
    elif policy == "all" or (policy == "privileged" and is_priv):
        kind = "mfa_setup"
    else:
        kind = "full"

    ttl_hours = settings.session_ttl_hours if kind == "full" else 1
    sess, raw_token = await svc.create_session(
        user,
        ttl_hours=ttl_hours,
        kind=kind,
        ip=client_ip,
        user_agent=request.headers.get("user-agent"),
    )
    try:
        login_limiter.reset(key)
    except Exception:  # noqa: BLE001 — never let a limiter fault break a successful login
        logger.error("login rate-limiter reset failed", exc_info=True)
    await AuditService(session).record(
        actor_user_id=user.id, tenant_id=None,
        action=("auth.login" if kind == "full" else f"auth.login.{kind}"),
        target_type="session", target_id=str(sess.id), ip=client_ip, details={},
    )
    await session.commit()
    max_age = ttl_hours * 3600
    response.set_cookie(SESSION_COOKIE, raw_token, httponly=True, secure=True, samesite="lax", max_age=max_age)
    response.set_cookie(CSRF_COOKIE, sess.csrf_token, httponly=False, secure=True, samesite="lax", max_age=max_age)
    if kind == "mfa_pending":
        return LoginOut(status="mfa_required")
    if kind == "mfa_setup":
        return LoginOut(
            status="mfa_setup_required",
            user=MeOut(
                id=user.id, email=user.email, name=user.name,
                is_superadmin=user.is_superadmin, mfa_setup_required=True,
            ),
        )
    return LoginOut(
        status="ok",
        user=MeOut(id=user.id, email=user.email, name=user.name, is_superadmin=user.is_superadmin),
    )


@router.post("/login/mfa", response_model=LoginOut, dependencies=[Depends(enforce_csrf)])
async def login_mfa(
    body: CodeIn,
    request: Request,
    response: Response,
    sess: Session = Depends(get_current_session),
    session: AsyncSession = Depends(get_session),
) -> LoginOut:
    if sess.kind != "mfa_pending":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No MFA challenge")
    client_ip = _client_ip(request)
    key = f"mfa|{sess.user_id}|{client_ip or '?'}"
    allowed, retry = login_limiter.check(key)
    if not allowed:
        raise HTTPException(
            status_code=429, detail="Too many attempts", headers={"Retry-After": str(retry)}
        )

    user = await AuthService(session).get_user_for_session(sess)
    row = await session.get(UserMfa, sess.user_id)
    if user is None or row is None or not row.enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No MFA challenge")

    secret = crypto.decrypt(row.totp_secret_enc)
    ok, step = mfa_svc.verify_totp(secret, body.code, last_used_step=row.last_used_step)
    used_recovery = False
    if ok:
        row.last_used_step = step
    else:
        # recovery-code fallback (one-time)
        codes = (
            await session.execute(
                select(UserRecoveryCode).where(
                    UserRecoveryCode.user_id == user.id,
                    UserRecoveryCode.used_at.is_(None),
                )
            )
        ).scalars().all()
        idx = mfa_svc.find_recovery_match(body.code, [c.code_hash for c in codes])
        if idx is not None:
            codes[idx].used_at = datetime.now(UTC)
            used_recovery = True

    if not ok and not used_recovery:
        login_limiter.record_failure(key)
        await AuditService(session).record(
            actor_user_id=user.id, tenant_id=None, action="mfa.login_failed",
            target_type="session", target_id=str(sess.id), ip=client_ip, details={},
        )
        await session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid code")

    # upgrade: drop the pending session, mint a full one
    raw_old = request.cookies.get(SESSION_COOKIE)
    if raw_old:
        await AuthService(session).delete_session_by_token(raw_old)
    settings = get_settings()
    full, raw_token = await AuthService(session).create_session(
        user, ttl_hours=settings.session_ttl_hours, kind="full",
        ip=client_ip, user_agent=request.headers.get("user-agent"),
    )
    login_limiter.reset(key)
    await AuditService(session).record(
        actor_user_id=user.id, tenant_id=None,
        action=("mfa.recovery_used" if used_recovery else "mfa.login_success"),
        target_type="session", target_id=str(full.id), ip=client_ip, details={},
    )
    await session.commit()
    max_age = settings.session_ttl_hours * 3600
    response.set_cookie(SESSION_COOKIE, raw_token, httponly=True, secure=True, samesite="lax", max_age=max_age)
    response.set_cookie(CSRF_COOKIE, full.csrf_token, httponly=False, secure=True, samesite="lax", max_age=max_age)
    return LoginOut(
        status="ok",
        user=MeOut(id=user.id, email=user.email, name=user.name, is_superadmin=user.is_superadmin),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(enforce_csrf)])
async def logout(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        await AuthService(session).delete_session_by_token(raw)
        await AuditService(session).record(
            actor_user_id=user.id, tenant_id=None, action="auth.logout",
            target_type="session", target_id=None,
            ip=request.client.host if request.client else None, details={},
        )
        await session.commit()
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(CSRF_COOKIE)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(enforce_csrf)])
async def logout_all(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await AuthService(session).delete_all_sessions_for_user(user.id)
    await AuditService(session).record(
        actor_user_id=user.id, tenant_id=None, action="auth.logout_all",
        target_type="user", target_id=str(user.id),
        ip=request.client.host if request.client else None, details={},
    )
    await session.commit()
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(CSRF_COOKIE)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/sessions", response_model=list[SessionInfo])
async def list_sessions(
    user: User = Depends(get_current_user),
    current: Session = Depends(get_current_session),
    session: AsyncSession = Depends(get_session),
) -> list[SessionInfo]:
    rows = await AuthService(session).list_sessions_for_user(user.id)
    return [
        SessionInfo(
            id=r.id, created_at=r.created_at, last_seen_at=r.last_seen_at,
            expires_at=r.expires_at, ip=r.ip, user_agent=r.user_agent,
            current=(r.id == current.id),
        )
        for r in rows
    ]


@router.get("/me", response_model=MeOut)
async def me(ctx: tuple[User, Session] = Depends(get_enrollment_ctx)) -> MeOut:
    user, sess = ctx
    return MeOut(
        id=user.id, email=user.email, name=user.name,
        is_superadmin=user.is_superadmin, mfa_setup_required=(sess.kind == "mfa_setup"),
    )
