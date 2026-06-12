import pyotp
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.cli import reset_user_mfa
from app.core import crypto
from app.models.audit import AuditLog
from app.models.user_mfa import UserMfa
from app.models.user_recovery_code import UserRecoveryCode
from app.services import mfa as mfa_svc
from tests.factories import make_user


async def _seed_mfa_user(db_engine, email="u@x.io"):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        user = await make_user(s, email=email, password="pw12345-secure")
        s.add(
            UserMfa(
                user_id=user.id,
                enabled=True,
                totp_secret_enc=crypto.encrypt(pyotp.random_base32()),
            )
        )
        _, hashes = mfa_svc.generate_recovery_codes(3)
        for h in hashes:
            s.add(UserRecoveryCode(user_id=user.id, code_hash=h))
        await s.commit()
        return user.id


async def test_cli_reset_clears_mfa(db_engine):
    user_id = await _seed_mfa_user(db_engine)
    n = await reset_user_mfa("u@x.io", engine=db_engine)
    assert n == 1
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        assert await s.get(UserMfa, user_id) is None
        remaining = (
            await s.execute(
                select(func.count())
                .select_from(UserRecoveryCode)
                .where(UserRecoveryCode.user_id == user_id)
            )
        ).scalar()
        assert remaining == 0


async def test_cli_reset_unknown_email_is_zero(db_engine):
    n = await reset_user_mfa("nobody@x.io", engine=db_engine)
    assert n == 0


async def test_cli_reset_writes_audit_record(db_engine):
    user_id = await _seed_mfa_user(db_engine, email="audit@x.io")
    await reset_user_mfa("audit@x.io", engine=db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        row = (
            await s.execute(select(AuditLog).where(AuditLog.action == "mfa.cli_reset"))
        ).scalar_one()
        assert row.actor_user_id is None
        assert row.target_type == "user"
        assert row.target_id == str(user_id)
        assert row.details == {"email": "audit@x.io"}
