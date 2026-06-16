import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn.helpers import base64url_to_bytes

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
from app.models.webauthn_credential import WebAuthnCredential
from app.schemas.auth import LoginIn, LoginOut, MeOut, SessionInfo
from app.schemas.mfa import CodeIn, WebAuthnLoginCompleteIn
from app.services import mfa as mfa_svc
from app.services import webauthn as wa
from app.services.app_settings import get_mfa_policy
from app.services.audit import AuditService
from app.services.auth import AuthService
from app.services.runtime_settings import get_runtime_config_or_defaults
from app.services.webauthn import has_webauthn_credentials
from app.services.webauthn_settings import get_webauthn_config

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

    # Brute-force thresholds + the session TTL are runtime-tunable (System page); read once per login.
    runtime = await get_runtime_config_or_defaults(session)

    # Fail CLOSED on a limiter fault: this gates credential validation, so a transient limiter error
    # must NOT silently disable brute-force protection. Brief 503 unavailability is the safer failure
    # mode; the limiter is in-process + memory-bounded, so a fault here is a genuine (rare) defect we
    # want to surface. Always log it so operators can detect a degraded defense.
    try:
        allowed, retry = login_limiter.check(
            key,
            max_attempts=runtime["login_max_attempts"],
            window_seconds=runtime["login_lockout_window_seconds"],
        )
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
    # enrolled -> setup-only (mfa_setup); otherwise a normal full session. "Enrolled" means a
    # confirmed TOTP OR at least one registered passkey (either can satisfy the challenge).
    mfa_row = await session.get(UserMfa, user.id)
    has_totp = bool(mfa_row and mfa_row.enabled)
    has_passkey = await has_webauthn_credentials(session, user.id)
    policy = await get_mfa_policy(session)
    is_priv = user.is_superadmin  # privileged = superadmin (tenant_admin membership is a later refinement)
    # The methods the user can use to clear an mfa_pending challenge (used only when kind is mfa_pending).
    methods = [m for m, present in (("totp", has_totp), ("webauthn", has_passkey)) if present]
    if has_totp or has_passkey:
        kind = "mfa_pending"
    elif policy == "all" or (policy == "privileged" and is_priv):
        kind = "mfa_setup"
    else:
        kind = "full"

    # mfa_pending is a short challenge window (minutes); mfa_setup keeps the 1h enrollment window;
    # full uses the normal session TTL. create_session accepts a fractional ttl_hours.
    if kind == "full":
        ttl_hours: float = runtime["session_ttl_hours"]
    elif kind == "mfa_pending":
        ttl_hours = settings.mfa_pending_ttl_minutes / 60
    else:  # mfa_setup
        ttl_hours = 1
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
    # max_age must be an integer number of seconds (a float yields a malformed Set-Cookie that
    # clients drop); round the fractional mfa_pending TTL up.
    max_age = round(ttl_hours * 3600)
    response.set_cookie(SESSION_COOKIE, raw_token, httponly=True, secure=True, samesite="lax", max_age=max_age)
    response.set_cookie(CSRF_COOKIE, sess.csrf_token, httponly=False, secure=True, samesite="lax", max_age=max_age)
    if kind == "mfa_pending":
        return LoginOut(status="mfa_required", methods=methods)
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
    runtime = await get_runtime_config_or_defaults(session)
    # Fail CLOSED on a limiter fault, mirroring /api/login: this gates MFA verification, so a
    # transient limiter error must not silently disable brute-force protection.
    try:
        allowed, retry = login_limiter.check(
            key,
            max_attempts=runtime["login_max_attempts"],
            window_seconds=runtime["login_lockout_window_seconds"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("mfa rate-limiter check failed; failing closed", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Login temporarily unavailable",
            headers={"Retry-After": "5"},
        ) from exc
    if not allowed:
        raise HTTPException(
            status_code=429, detail="Too many attempts", headers={"Retry-After": str(retry)}
        )

    user = await AuthService(session).get_user_for_session(sess)
    # FOR UPDATE: serialize concurrent verifications for this user so the TOTP anti-replay
    # (last_used_step) check-then-set cannot race between requests.
    row = (
        await session.execute(
            select(UserMfa).where(UserMfa.user_id == sess.user_id).with_for_update()
        )
    ).scalar_one_or_none()
    if user is None or row is None or not row.enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No MFA challenge")

    secret = crypto.decrypt(row.totp_secret_enc)
    ok, step = mfa_svc.verify_totp(secret, body.code, last_used_step=row.last_used_step)
    used_recovery = False
    if ok:
        row.last_used_step = step
    else:
        # recovery-code fallback (one-time). Argon2 hashes can't be matched in SQL, so first find
        # the matching unused code by id, THEN consume it with an atomic guarded UPDATE. Only this
        # request — not a concurrent one — succeeds if the UPDATE returns a row.
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
            consumed = (
                await session.execute(
                    update(UserRecoveryCode)
                    .where(
                        UserRecoveryCode.id == codes[idx].id,
                        UserRecoveryCode.used_at.is_(None),
                    )
                    .values(used_at=datetime.now(UTC))
                    .returning(UserRecoveryCode.id)
                )
            ).scalar_one_or_none()
            used_recovery = consumed is not None

    if not ok and not used_recovery:
        try:
            login_limiter.record_failure(key)
        except Exception:  # noqa: BLE001 — never let a limiter fault turn a 401 into a 500
            logger.error("mfa rate-limiter record_failure failed", exc_info=True)
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
    full, raw_token = await AuthService(session).create_session(
        user, ttl_hours=runtime["session_ttl_hours"], kind="full",
        ip=client_ip, user_agent=request.headers.get("user-agent"),
    )
    try:
        login_limiter.reset(key)
    except Exception:  # noqa: BLE001 — never let a limiter fault break a successful MFA login
        logger.error("mfa rate-limiter reset failed", exc_info=True)
    await AuditService(session).record(
        actor_user_id=user.id, tenant_id=None,
        action=("mfa.recovery_used" if used_recovery else "mfa.login_success"),
        target_type="session", target_id=str(full.id), ip=client_ip, details={},
    )
    await session.commit()
    max_age = round(runtime["session_ttl_hours"] * 3600)
    response.set_cookie(SESSION_COOKIE, raw_token, httponly=True, secure=True, samesite="lax", max_age=max_age)
    response.set_cookie(CSRF_COOKIE, full.csrf_token, httponly=False, secure=True, samesite="lax", max_age=max_age)
    return LoginOut(
        status="ok",
        user=MeOut(id=user.id, email=user.email, name=user.name, is_superadmin=user.is_superadmin),
    )


@router.post("/login/webauthn/begin")
async def login_webauthn_begin(
    sess: Session = Depends(get_current_session),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Authentication options for the pending user's registered passkeys. Stores the challenge on the
    mfa_pending session (single-use). 409 if WebAuthn is unconfigured."""
    if sess.kind != "mfa_pending":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No MFA challenge")
    cfg = await get_webauthn_config(session)
    if not cfg.is_configured():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="WebAuthn not configured")
    creds = (
        await session.execute(
            select(WebAuthnCredential).where(WebAuthnCredential.user_id == sess.user_id)
        )
    ).scalars().all()
    opts_json, challenge = wa.authentication_options(
        rp_id=cfg.rp_id, allow_cred_ids=[c.credential_id for c in creds]
    )
    sess.webauthn_challenge = challenge
    await session.commit()
    return json.loads(opts_json)


@router.post(
    "/login/webauthn/complete", response_model=LoginOut, dependencies=[Depends(enforce_csrf)]
)
async def login_webauthn_complete(
    body: WebAuthnLoginCompleteIn,
    request: Request,
    response: Response,
    sess: Session = Depends(get_current_session),
    session: AsyncSession = Depends(get_session),
) -> LoginOut:
    if sess.kind != "mfa_pending":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No MFA challenge")
    if not sess.webauthn_challenge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No pending WebAuthn challenge"
        )
    client_ip = _client_ip(request)
    key = f"mfa|{sess.user_id}|{client_ip or '?'}"
    runtime = await get_runtime_config_or_defaults(session)
    # Fail CLOSED on a limiter fault, mirroring /api/login/mfa.
    try:
        allowed, retry = login_limiter.check(
            key,
            max_attempts=runtime["login_max_attempts"],
            window_seconds=runtime["login_lockout_window_seconds"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("webauthn rate-limiter check failed; failing closed", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Login temporarily unavailable",
            headers={"Retry-After": "5"},
        ) from exc
    if not allowed:
        raise HTTPException(
            status_code=429, detail="Too many attempts", headers={"Retry-After": str(retry)}
        )

    cfg = await get_webauthn_config(session)
    if not cfg.is_configured():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="WebAuthn not configured")
    user = await AuthService(session).get_user_for_session(sess)
    # Resolve the asserted credential by its raw id, scoped to this user. FOR UPDATE serializes the
    # sign-count check-then-set against a concurrent assertion for the same credential.
    raw_id = body.credential.get("rawId") or body.credential.get("id")
    cred = None
    if user is not None and isinstance(raw_id, str):
        try:
            cred_id_bytes = base64url_to_bytes(raw_id)
        except Exception:  # noqa: BLE001 — a malformed id is just a failed assertion
            cred_id_bytes = None
        if cred_id_bytes is not None:
            cred = (
                await session.execute(
                    select(WebAuthnCredential)
                    .where(
                        WebAuthnCredential.user_id == user.id,
                        WebAuthnCredential.credential_id == cred_id_bytes,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()

    new_count = None
    if cred is not None:
        try:
            new_count = wa.verify_authentication(
                response=body.credential, challenge=sess.webauthn_challenge,
                rp_id=cfg.rp_id, origin=cfg.origin,
                public_key=cred.public_key, sign_count=cred.sign_count,
            )
        except wa.WebAuthnError:
            new_count = None

    if user is None or cred is None or new_count is None:
        # Single-use: burn the challenge on any failure so it can't be retried.
        sess.webauthn_challenge = None
        try:
            login_limiter.record_failure(key)
        except Exception:  # noqa: BLE001 — never let a limiter fault turn a 401 into a 500
            logger.error("webauthn rate-limiter record_failure failed", exc_info=True)
        await AuditService(session).record(
            actor_user_id=(user.id if user else None), tenant_id=None, action="mfa.login_failed",
            target_type="session", target_id=str(sess.id), ip=client_ip, details={"method": "webauthn"},
        )
        await session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid passkey")

    cred.sign_count = new_count
    cred.last_used_at = datetime.now(UTC)
    sess.webauthn_challenge = None  # single-use

    # Upgrade: drop the pending session, mint a fresh full one (anti-fixation rotation), set cookies —
    # exactly as /login/mfa does.
    raw_old = request.cookies.get(SESSION_COOKIE)
    if raw_old:
        await AuthService(session).delete_session_by_token(raw_old)
    full, raw_token = await AuthService(session).create_session(
        user, ttl_hours=runtime["session_ttl_hours"], kind="full",
        ip=client_ip, user_agent=request.headers.get("user-agent"),
    )
    try:
        login_limiter.reset(key)
    except Exception:  # noqa: BLE001 — never let a limiter fault break a successful login
        logger.error("webauthn rate-limiter reset failed", exc_info=True)
    await AuditService(session).record(
        actor_user_id=user.id, tenant_id=None, action="mfa.login_success",
        target_type="session", target_id=str(full.id), ip=client_ip, details={"method": "webauthn"},
    )
    await session.commit()
    max_age = round(runtime["session_ttl_hours"] * 3600)
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
    sess: Session = Depends(get_current_session),
    session: AsyncSession = Depends(get_session),
) -> Response:
    # get_current_session accepts any valid session kind, so a user mid-MFA (mfa_pending/mfa_setup)
    # can cancel and invalidate the cookie rather than being stuck behind get_current_user's 403.
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        await AuthService(session).delete_session_by_token(raw)
        await AuditService(session).record(
            actor_user_id=sess.user_id, tenant_id=None, action="auth.logout",
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
    sess: Session = Depends(get_current_session),
    session: AsyncSession = Depends(get_session),
) -> Response:
    # Accept any valid session kind (incl. mfa_pending/mfa_setup) so a mid-MFA user can revoke
    # all of their sessions.
    await AuthService(session).delete_all_sessions_for_user(sess.user_id)
    await AuditService(session).record(
        actor_user_id=sess.user_id, tenant_id=None, action="auth.logout_all",
        target_type="user", target_id=str(sess.user_id),
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
