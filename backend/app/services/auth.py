import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import verify_password
from app.models.session import Session
from app.models.user import User

# Persist last_seen at most once per minute per session to bound write amplification
# while still enforcing the idle timeout at ~60s granularity.
_LAST_SEEN_THROTTLE = timedelta(seconds=60)


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def authenticate(self, email: str, password: str) -> User | None:
        result = await self.session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user is None or user.status != "active":
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user

    async def create_session(
        self,
        user: User,
        *,
        ttl_hours: int,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[Session, str]:
        """Create a session. Returns (session, raw_token). Only the hash is stored."""
        now = datetime.now(UTC)
        raw_token = secrets.token_urlsafe(32)
        sess = Session(
            user_id=user.id,
            token_hash=_hash_token(raw_token),
            csrf_token=secrets.token_urlsafe(32),
            last_seen_at=now,
            expires_at=now + timedelta(hours=ttl_hours),
            ip=ip,
            user_agent=(user_agent[:512] if user_agent else None),
        )
        self.session.add(sess)
        user.last_login = now
        await self.session.flush()
        return sess, raw_token

    async def get_session_for_token(self, raw_token: str) -> Session | None:
        """Resolve+validate a session from its raw token (absolute + idle expiry)."""
        now = datetime.now(UTC)
        idle = timedelta(minutes=get_settings().session_idle_minutes)
        result = await self.session.execute(
            select(Session).where(Session.token_hash == _hash_token(raw_token))
        )
        sess = result.scalar_one_or_none()
        if sess is None:
            return None
        if sess.expires_at <= now or (now - sess.last_seen_at) > idle:
            return None
        if (now - sess.last_seen_at) >= _LAST_SEEN_THROTTLE:
            sess.last_seen_at = now
            # get_session() does not auto-commit, so persist the touch here. This relies on an
            # ordering invariant: get_current_session (which calls this) is the FIRST DB-touching
            # dependency in the request, so committing now cannot clobber uncommitted writes or the
            # transaction-local RLS context (set later by tenant_context via set_tenant_context).
            # If a DB dependency is ever ordered before get_current_session, revisit this.
            await self.session.commit()
        return sess

    async def get_user_for_session(self, sess: Session) -> User | None:
        return await self.session.get(User, sess.user_id)

    async def delete_session_by_token(self, raw_token: str) -> None:
        await self.session.execute(
            delete(Session).where(Session.token_hash == _hash_token(raw_token))
        )

    async def delete_all_sessions_for_user(self, user_id: uuid.UUID) -> None:
        await self.session.execute(delete(Session).where(Session.user_id == user_id))

    async def list_sessions_for_user(self, user_id: uuid.UUID) -> list[Session]:
        result = await self.session.execute(
            select(Session)
            .where(Session.user_id == user_id)
            .order_by(Session.last_seen_at.desc())
        )
        return list(result.scalars().all())

    async def purge_expired(self, now: datetime) -> int:
        idle = timedelta(minutes=get_settings().session_idle_minutes)
        result = await self.session.execute(
            delete(Session).where(
                (Session.expires_at <= now) | (Session.last_seen_at < now - idle)
            )
        )
        return result.rowcount or 0
