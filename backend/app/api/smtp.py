from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import enforce_csrf, require_org
from app.core.rbac import Action
from app.models.user import User
from app.schemas.smtp import SECURITIES, SmtpSettingsIn, SmtpSettingsOut, SmtpTestIn, SmtpTestOut
from app.services.audit import AuditService
from app.services.email.smtp import EmailSendError, SmtpSendConfig, send_report_email
from app.services.smtp_settings import SmtpSettingsService

router = APIRouter(prefix="/api/admin/smtp", tags=["smtp"])


def _out(row) -> SmtpSettingsOut:
    if row is None:
        return SmtpSettingsOut(enabled=False, host="", port=587, security="starttls",
                               username=None, from_email="", from_name="", has_password=False)
    return SmtpSettingsOut(
        enabled=row.enabled, host=row.host, port=row.port, security=row.security,
        username=row.username, from_email=row.from_email, from_name=row.from_name,
        has_password=row.password_enc is not None,
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
    password = body.password
    if password is None:
        stored = await svc.get()
        password = svc.to_send_config(stored).password if (stored and stored.password_enc) else None
    cfg = SmtpSendConfig(
        host=body.host, port=body.port, security=body.security, username=body.username,
        password=password, from_email=str(body.from_email), from_name=body.from_name,
    )
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
