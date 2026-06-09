import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.membership import Membership
from app.models.tenant import Tenant
from app.models.user import User


async def make_user(
    session: AsyncSession,
    *,
    email: str,
    password: str = "pw",
    is_superadmin: bool = False,
    name: str = "Test User",
) -> User:
    user = User(
        email=email,
        name=name,
        password_hash=hash_password(password),
        is_superadmin=is_superadmin,
    )
    session.add(user)
    await session.flush()
    return user


async def make_tenant(session: AsyncSession, *, slug: str, name: str = "Tenant") -> Tenant:
    tenant = Tenant(name=name, slug=slug)
    session.add(tenant)
    await session.flush()
    return tenant


async def make_membership(
    session: AsyncSession, *, user_id: uuid.UUID, tenant_id: uuid.UUID, role: str
) -> Membership:
    m = Membership(user_id=user_id, tenant_id=tenant_id, role=role)
    session.add(m)
    await session.flush()
    return m
