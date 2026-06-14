from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import hash_password
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.auth import MeOut, SetupIn
from app.services.audit import AuditService

router = APIRouter(prefix="/api", tags=["setup"])


@router.post("/setup", response_model=MeOut, status_code=status.HTTP_201_CREATED)
async def setup(
    payload: SetupIn, request: Request, session: AsyncSession = Depends(get_session)
) -> User:
    repo = UserRepository(session)
    if await repo.count() > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Setup already completed: at least one user already exists.",
        )
    user = User(
        email=payload.email,
        name=payload.name,
        password_hash=hash_password(payload.password),
        is_superadmin=True,
    )
    await repo.add(user)
    await AuditService(session).record(
        actor_user_id=user.id, tenant_id=None, action="setup.bootstrap",
        target_type="user", target_id=str(user.id),
        ip=request.client.host if request.client else None,
        details={"email": payload.email},
    )
    await session.commit()
    return user
