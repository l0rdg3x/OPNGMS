import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        actor_user_id: uuid.UUID | None,
        tenant_id: uuid.UUID | None,
        action: str,
        target_type: str | None = None,
        target_id: str | None = None,
        ip: str | None = None,
        details: dict | None = None,
    ) -> None:
        self.session.add(
            AuditLog(
                actor_user_id=actor_user_id,
                tenant_id=tenant_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                ip=ip,
                details=details or {},
            )
        )
        await self.session.flush()
