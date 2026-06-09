from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import enforce_csrf, require_org
from app.core.rbac import Action
from app.core.security import hash_password
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.user import UserCreateIn, UserOut
from app.services.audit import AuditService

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("", response_model=list[UserOut])
async def list_users(
    user: User = Depends(require_org(Action.USER_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> list[User]:
    return await UserRepository(session).list()


@router.post(
    "",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_csrf)],
)
async def create_user(
    payload: UserCreateIn,
    request: Request,
    actor: User = Depends(require_org(Action.USER_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> User:
    repo = UserRepository(session)
    if await repo.get_by_email(payload.email) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already in use")
    new_user = await repo.add(
        User(
            email=payload.email,
            name=payload.name,
            password_hash=hash_password(payload.password),
            is_superadmin=payload.is_superadmin,
        )
    )
    await AuditService(session).record(
        actor_user_id=actor.id,
        tenant_id=None,
        action="user.create",
        target_type="user",
        target_id=str(new_user.id),
        ip=request.client.host if request.client else None,
        details={"email": new_user.email, "is_superadmin": new_user.is_superadmin},
    )
    await session.commit()
    return new_user
