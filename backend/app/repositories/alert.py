import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert


class AlertRepository:
    """Letture alert per tenant. Doppio isolamento: filtro tenant_id + RLS."""

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def list(self, *, active_only: bool) -> Sequence[Alert]:
        stmt = select(Alert).where(Alert.tenant_id == self.tenant_id)
        if active_only:
            stmt = stmt.where(Alert.resolved_at.is_(None))
        stmt = stmt.order_by(Alert.opened_at.desc())
        return (await self.session.execute(stmt)).scalars().all()
