import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import get_current_user
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.membership import MembershipRepository

router = APIRouter(prefix="/api/me", tags=["me"])


class MyTenantOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    role: str | None  # None per superadmin (accesso globale)


@router.get("/tenants", response_model=list[MyTenantOut])
async def my_tenants(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[MyTenantOut]:
    if user.is_superadmin:
        tenants = (
            (await session.execute(select(Tenant).order_by(Tenant.slug))).scalars().all()
        )
        return [MyTenantOut(id=t.id, name=t.name, slug=t.slug, role=None) for t in tenants]
    memberships = await MembershipRepository(session).list_for_user(user.id)
    by_id: dict[uuid.UUID, str] = {m.tenant_id: m.role for m in memberships}
    if not by_id:
        return []
    tenants = (
        (
            await session.execute(
                select(Tenant).where(Tenant.id.in_(by_id.keys())).order_by(Tenant.slug)
            )
        )
        .scalars()
        .all()
    )
    return [MyTenantOut(id=t.id, name=t.name, slug=t.slug, role=by_id[t.id]) for t in tenants]
