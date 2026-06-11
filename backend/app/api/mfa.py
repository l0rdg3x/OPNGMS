import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.db import get_session
from app.core.deps import enforce_csrf, get_current_user, get_enrollment_ctx, require_org
from app.core.rbac import Action
from app.core.security import verify_password
from app.models.user import User
from app.models.user_mfa import UserMfa
from app.models.user_recovery_code import UserRecoveryCode
from app.schemas.mfa import (
    CodeIn,
    MfaPolicyIn,
    MfaPolicyOut,
    MfaStatusOut,
    PasswordIn,
    RecoveryOut,
    SetupOut,
)
from app.services import mfa as mfa_svc
from app.services.app_settings import MFA_MODES, get_mfa_policy, set_mfa_policy
from app.services.audit import AuditService

router = APIRouter(prefix="/api", tags=["mfa"])


def _require_password(user: User, password: str) -> None:
    if not verify_password(password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Password required")


async def _mfa_row(session: AsyncSession, user_id) -> UserMfa | None:
    return await session.get(UserMfa, user_id)


@router.get("/me/mfa", response_model=MfaStatusOut)
async def mfa_status(
    ctx=Depends(get_enrollment_ctx), session: AsyncSession = Depends(get_session)
) -> MfaStatusOut:
    user, _ = ctx
    row = await _mfa_row(session, user.id)
    remaining = (
        await session.execute(
            select(func.count())
            .select_from(UserRecoveryCode)
            .where(
                UserRecoveryCode.user_id == user.id,
                UserRecoveryCode.used_at.is_(None),
            )
        )
    ).scalar() or 0
    return MfaStatusOut(enabled=bool(row and row.enabled), recovery_codes_remaining=int(remaining))


@router.post("/me/mfa/setup", response_model=SetupOut, dependencies=[Depends(enforce_csrf)])
async def mfa_setup(
    body: PasswordIn,
    ctx=Depends(get_enrollment_ctx),
    session: AsyncSession = Depends(get_session),
) -> SetupOut:
    user, _ = ctx
    _require_password(user, body.password)
    secret = mfa_svc.new_secret()
    row = await _mfa_row(session, user.id)
    if row is None:
        row = UserMfa(user_id=user.id)
        session.add(row)
    row.enabled = False
    row.totp_secret_enc = crypto.encrypt(secret)
    row.confirmed_at = None
    row.last_used_step = None
    await session.commit()
    return SetupOut(otpauth_uri=mfa_svc.provisioning_uri(secret, user.email), secret=secret)


@router.post("/me/mfa/confirm", response_model=RecoveryOut, dependencies=[Depends(enforce_csrf)])
async def mfa_confirm(
    body: CodeIn,
    ctx=Depends(get_enrollment_ctx),
    session: AsyncSession = Depends(get_session),
) -> RecoveryOut:
    user, sess = ctx
    row = await _mfa_row(session, user.id)
    if row is None or not row.totp_secret_enc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No pending enrollment")
    secret = crypto.decrypt(row.totp_secret_enc)
    ok, step = mfa_svc.verify_totp(secret, body.code, last_used_step=row.last_used_step)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Invalid code"
        )
    row.enabled = True
    row.confirmed_at = datetime.now(UTC)
    row.last_used_step = step
    # fresh recovery codes
    await session.execute(
        UserRecoveryCode.__table__.delete().where(UserRecoveryCode.user_id == user.id)
    )
    codes, hashes = mfa_svc.generate_recovery_codes(10)
    for h in hashes:
        session.add(UserRecoveryCode(user_id=user.id, code_hash=h))
    # if this session was setup-only, upgrade it to full now that MFA is enrolled
    if sess.kind == "mfa_setup":
        sess.kind = "full"
    await AuditService(session).record(
        actor_user_id=user.id, tenant_id=None, action="mfa.confirm",
        target_type="user", target_id=str(user.id), ip=None, details={},
    )
    await session.commit()
    return RecoveryOut(recovery_codes=codes)


@router.post(
    "/me/mfa/disable",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(enforce_csrf)],
)
async def mfa_disable(
    body: PasswordIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    _require_password(user, body.password)
    await session.execute(
        UserRecoveryCode.__table__.delete().where(UserRecoveryCode.user_id == user.id)
    )
    row = await _mfa_row(session, user.id)
    if row is not None:
        await session.delete(row)
    await AuditService(session).record(
        actor_user_id=user.id, tenant_id=None, action="mfa.disable",
        target_type="user", target_id=str(user.id), ip=None, details={},
    )
    await session.commit()


@router.post(
    "/me/mfa/recovery/regenerate",
    response_model=RecoveryOut,
    dependencies=[Depends(enforce_csrf)],
)
async def mfa_regen(
    body: PasswordIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> RecoveryOut:
    _require_password(user, body.password)
    row = await _mfa_row(session, user.id)
    if row is None or not row.enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA not enabled")
    await session.execute(
        UserRecoveryCode.__table__.delete().where(UserRecoveryCode.user_id == user.id)
    )
    codes, hashes = mfa_svc.generate_recovery_codes(10)
    for h in hashes:
        session.add(UserRecoveryCode(user_id=user.id, code_hash=h))
    await AuditService(session).record(
        actor_user_id=user.id, tenant_id=None, action="mfa.recovery_regenerate",
        target_type="user", target_id=str(user.id), ip=None, details={},
    )
    await session.commit()
    return RecoveryOut(recovery_codes=codes)


# --- Superadmin: global MFA policy + admin reset of another user's MFA ---


@router.get("/admin/mfa-policy", response_model=MfaPolicyOut)
async def mfa_policy_get(
    user: User = Depends(require_org(Action.USER_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> MfaPolicyOut:
    return MfaPolicyOut(mode=await get_mfa_policy(session))


@router.put("/admin/mfa-policy", response_model=MfaPolicyOut, dependencies=[Depends(enforce_csrf)])
async def mfa_policy_set(
    body: MfaPolicyIn,
    user: User = Depends(require_org(Action.USER_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> MfaPolicyOut:
    if body.mode not in MFA_MODES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid mode")
    await set_mfa_policy(session, body.mode)
    await AuditService(session).record(
        actor_user_id=user.id, tenant_id=None, action="mfa.policy_change",
        target_type="app_settings", target_id="mfa_required", ip=None,
        details={"mode": body.mode},
    )
    await session.commit()
    return MfaPolicyOut(mode=body.mode)


@router.post(
    "/users/{user_id}/mfa/reset",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(enforce_csrf)],
)
async def mfa_admin_reset(
    user_id: uuid.UUID,
    actor: User = Depends(require_org(Action.USER_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> None:
    await session.execute(
        UserRecoveryCode.__table__.delete().where(UserRecoveryCode.user_id == user_id)
    )
    row = await session.get(UserMfa, user_id)
    if row is not None:
        await session.delete(row)
    await AuditService(session).record(
        actor_user_id=actor.id, tenant_id=None, action="mfa.admin_reset",
        target_type="user", target_id=str(user_id), ip=None, details={},
    )
    await session.commit()
