import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_password
from app.models.session import Session
from app.models.user import User


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

    async def create_session(self, user: User, ttl_hours: int) -> Session:
        now = datetime.now(timezone.utc)
        sess = Session(user_id=user.id, expires_at=now + timedelta(hours=ttl_hours))
        self.session.add(sess)
        user.last_login = now
        await self.session.flush()
        return sess

    async def get_user_for_session(self, session_id: uuid.UUID) -> User | None:
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            select(Session).where(Session.id == session_id)
        )
        sess = result.scalar_one_or_none()
        if sess is None or sess.expires_at <= now:
            return None
        return await self.session.get(User, sess.user_id)

    async def delete_session(self, session_id: uuid.UUID) -> None:
        await self.session.execute(delete(Session).where(Session.id == session_id))
