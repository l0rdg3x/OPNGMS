"""Host-level admin CLI (break-glass). Usage: python -m app.cli mfa-reset --email <email>.

Connects via ADMIN_DATABASE_URL (owner role) and clears a user's MFA + recovery codes. This is the
recovery path for the last superadmin locked out of the web UI."""
import argparse
import asyncio

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.config import get_settings
from app.core.db import make_engine
from app.models.user import User
from app.models.user_mfa import UserMfa
from app.models.user_recovery_code import UserRecoveryCode


async def reset_user_mfa(email: str, *, engine: AsyncEngine | None = None) -> int:
    """Clear a user's MFA + recovery codes. Returns the number of users affected (0 or 1)."""
    if engine is None:
        dsn = get_settings().admin_database_url
        if not dsn:
            raise RuntimeError("ADMIN_DATABASE_URL is not configured")
        eng = make_engine(dsn)
    else:
        eng = engine
    factory = async_sessionmaker(eng, expire_on_commit=False)
    async with factory() as s:
        user = (await s.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            if engine is None:
                await eng.dispose()
            return 0
        await s.execute(delete(UserRecoveryCode).where(UserRecoveryCode.user_id == user.id))
        await s.execute(delete(UserMfa).where(UserMfa.user_id == user.id))
        await s.commit()
    if engine is None:
        await eng.dispose()
    return 1


def main() -> None:
    p = argparse.ArgumentParser(prog="app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("mfa-reset")
    r.add_argument("--email", required=True)
    args = p.parse_args()
    if args.cmd == "mfa-reset":
        n = asyncio.run(reset_user_mfa(args.email))
        print(f"MFA reset for {args.email}: {n} user(s) affected")


if __name__ == "__main__":
    main()
