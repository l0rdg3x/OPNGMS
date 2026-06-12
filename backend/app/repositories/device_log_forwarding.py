import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device_log_forwarding import DeviceLogForwarding


class DeviceLogForwardingRepository:
    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def get(self, device_id: uuid.UUID) -> DeviceLogForwarding | None:
        return (await self.session.execute(
            select(DeviceLogForwarding).where(
                DeviceLogForwarding.tenant_id == self.tenant_id,
                DeviceLogForwarding.device_id == device_id)
        )).scalar_one_or_none()
