from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import hash_password
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.auth import MeOut, SetupIn

router = APIRouter(prefix="/api", tags=["setup"])


@router.post("/setup", response_model=MeOut, status_code=status.HTTP_201_CREATED)
async def setup(payload: SetupIn, session: AsyncSession = Depends(get_session)) -> User:
    repo = UserRepository(session)
    if await repo.count() > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Setup gia' completato: esiste gia' almeno un utente.",
        )
    user = User(
        email=payload.email,
        name=payload.name,
        password_hash=hash_password(payload.password),
        is_superadmin=True,
    )
    await repo.add(user)
    await session.commit()
    return user
