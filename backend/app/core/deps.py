import uuid

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models.user import User
from app.services.auth import AuthService

SESSION_COOKIE = "opngms_session"


async def get_current_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> User:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Non autenticato")
    try:
        session_id = uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sessione non valida")
    user = await AuthService(session).get_user_for_session(session_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sessione scaduta")
    return user
