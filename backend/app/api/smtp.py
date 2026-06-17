from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import enforce_csrf, require_org
from app.core.rbac import Action
from app.models.user import User
from app.schemas.smtp import SECURITIES, SmtpSettingsIn, SmtpSettingsOut, SmtpTestIn, SmtpTestOut
from app.services.audit import AuditService
from app.services.email.oauth import (
    OAuthTokenError,
    build_authorize_url,
    exchange_code,
    sign_state,
    verify_state,
)
from app.services.email.smtp import EmailSendError, SmtpSendConfig, send_report_email
from app.services.smtp_settings import SmtpSettingsService

router = APIRouter(prefix="/api/admin/smtp", tags=["smtp"])


def _out(row) -> SmtpSettingsOut:
    if row is None:
        return SmtpSettingsOut(enabled=False, host="", port=587, security="starttls",
                               username=None, from_email="", from_name="", has_password=False,
                               auth_method="password", oauth_provider=None, oauth_client_id=None,
                               oauth_tenant_id=None, has_client_secret=False, has_refresh_token=False)
    return SmtpSettingsOut(
        enabled=row.enabled, host=row.host, port=row.port, security=row.security,
        username=row.username, from_email=row.from_email, from_name=row.from_name,
        has_password=row.password_enc is not None,
        auth_method=row.auth_method, oauth_provider=row.oauth_provider,
        oauth_client_id=row.oauth_client_id, oauth_tenant_id=row.oauth_tenant_id,
        has_client_secret=row.oauth_client_secret_enc is not None,
        has_refresh_token=row.oauth_refresh_token_enc is not None,
    )


@router.get("", response_model=SmtpSettingsOut)
async def get_smtp(
    user: User = Depends(require_org(Action.USER_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> SmtpSettingsOut:
    return _out(await SmtpSettingsService(session).get())


@router.put("", response_model=SmtpSettingsOut, dependencies=[Depends(enforce_csrf)])
async def put_smtp(
    body: SmtpSettingsIn,
    user: User = Depends(require_org(Action.USER_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> SmtpSettingsOut:
    if body.security not in SECURITIES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid security")
    svc = SmtpSettingsService(session)
    row = await svc.upsert(
        enabled=body.enabled, host=body.host, port=body.port, security=body.security,
        username=body.username, from_email=str(body.from_email), from_name=body.from_name,
        password=body.password, clear_password=body.clear_password,
        auth_method=body.auth_method, oauth_provider=body.oauth_provider,
        oauth_client_id=body.oauth_client_id, oauth_client_secret=body.oauth_client_secret,
        oauth_refresh_token=body.oauth_refresh_token, oauth_tenant_id=body.oauth_tenant_id,
        clear_client_secret=body.clear_client_secret, clear_refresh_token=body.clear_refresh_token,
    )
    await AuditService(session).record(
        actor_user_id=user.id, tenant_id=None, action="smtp.update",
        target_type="smtp_settings", target_id="1", ip=None,
        details={"host": body.host, "enabled": body.enabled},
    )
    out = _out(row)
    await session.commit()
    return out


@router.post("/test", response_model=SmtpTestOut, dependencies=[Depends(enforce_csrf)])
async def test_smtp(
    body: SmtpTestIn,
    user: User = Depends(require_org(Action.USER_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> SmtpTestOut:
    if body.security not in SECURITIES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid security")
    svc = SmtpSettingsService(session)
    stored = await svc.get()
    if body.auth_method == "oauth":
        from app.services.email.oauth import OAuthTokenError, fetch_access_token
        secret = body.oauth_client_secret or (
            crypto.decrypt(stored.oauth_client_secret_enc)
            if stored and stored.oauth_client_secret_enc else "")
        refresh = body.oauth_refresh_token or (
            crypto.decrypt(stored.oauth_refresh_token_enc)
            if stored and stored.oauth_refresh_token_enc else "")
        try:
            token = await fetch_access_token(body.oauth_provider or "", body.oauth_client_id or "",
                                             secret, refresh, body.oauth_tenant_id or "")
        except OAuthTokenError as exc:
            await session.commit()
            return SmtpTestOut(ok=False, detail=str(exc))
        cfg = SmtpSendConfig(host=body.host, port=body.port, security=body.security,
                             username=str(body.from_email), password=None, access_token=token,
                             from_email=str(body.from_email), from_name=body.from_name)
    else:
        password = body.password
        if password is None and stored and stored.password_enc:
            password = crypto.decrypt(stored.password_enc)
        cfg = SmtpSendConfig(host=body.host, port=body.port, security=body.security,
                             username=body.username, password=password,
                             from_email=str(body.from_email), from_name=body.from_name)
    await AuditService(session).record(
        actor_user_id=user.id, tenant_id=None, action="smtp.test",
        target_type="smtp_settings", target_id="1", ip=None, details={"to": str(body.to)},
    )
    await session.commit()
    try:
        await send_report_email(
            cfg, subject="OPNGMS SMTP test", recipients=[str(body.to)],
            body_text="This is a test email from OPNGMS. SMTP delivery is configured correctly.",
            attachment=("opngms-test.txt", b"OPNGMS SMTP test", "text/plain"),
        )
    except EmailSendError as exc:
        return SmtpTestOut(ok=False, detail=str(exc))
    return SmtpTestOut(ok=True, detail="sent")


_PROVIDERS = {"google", "microsoft"}


@router.get("/oauth/{provider}/authorize")
async def oauth_authorize(
    provider: str,
    user: User = Depends(require_org(Action.USER_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """EXPERIMENTAL/UNTESTED browser OAuth flow. Build the consent URL for the saved client id."""
    if provider not in _PROVIDERS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown provider")
    base = get_settings().public_base_url.rstrip("/")
    if not base:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="PUBLIC_BASE_URL not configured")
    row = await SmtpSettingsService(session).get()
    if row is None or not row.oauth_client_id or row.oauth_client_secret_enc is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="save the client id and secret first")
    redirect_uri = f"{base}/api/admin/smtp/oauth/{provider}/callback"
    state = sign_state(user.id, provider)
    try:
        url = build_authorize_url(provider, row.oauth_client_id, redirect_uri, state, row.oauth_tenant_id)
    except OAuthTokenError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"authorize_url": url}


@router.get("/oauth/{provider}/callback")
async def oauth_callback(
    provider: str,
    request: Request,
    user: User = Depends(require_org(Action.USER_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """EXPERIMENTAL/UNTESTED. The provider's browser redirect lands here (superadmin session via the
    SameSite=Lax cookie). The signed `state` is the CSRF defence — it binds the initiating superadmin +
    provider and expires in 10 min; combined with the provider-single-use `code`, a replayed state alone
    cannot do anything. Any failure redirects with ?oauth=error (never surfaces token material)."""
    if provider not in _PROVIDERS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown provider")
    base = get_settings().public_base_url.rstrip("/")
    if not base:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="PUBLIC_BASE_URL not configured")
    landing_ok = f"{base}/admin/smtp?oauth=success"
    landing_err = f"{base}/admin/smtp?oauth=error"
    code = request.query_params.get("code")
    state = request.query_params.get("state") or ""
    if not code or not verify_state(state, user.id, provider):
        return RedirectResponse(landing_err, status_code=status.HTTP_302_FOUND)
    svc = SmtpSettingsService(session)
    row = await svc.get()
    if row is None or not row.oauth_client_id or row.oauth_client_secret_enc is None:
        return RedirectResponse(landing_err, status_code=status.HTTP_302_FOUND)
    redirect_uri = f"{base}/api/admin/smtp/oauth/{provider}/callback"
    try:
        result = await exchange_code(
            provider, row.oauth_client_id, crypto.decrypt(row.oauth_client_secret_enc),
            code, redirect_uri, row.oauth_tenant_id)
    except OAuthTokenError:
        return RedirectResponse(landing_err, status_code=status.HTTP_302_FOUND)
    await svc.store_oauth_refresh_token(provider, result["refresh_token"])
    await AuditService(session).record(
        actor_user_id=user.id, tenant_id=None, action="smtp.oauth.connected",
        target_type="smtp_settings", target_id="1", ip=None, details={"provider": provider},
    )
    await session.commit()
    return RedirectResponse(landing_ok, status_code=status.HTTP_302_FOUND)
