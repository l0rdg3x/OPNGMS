import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device


class DeviceRepository:
    """Accesso ai device già scoperto per tenant a livello applicativo.

    Doppio livello di isolamento: il filtro `tenant_id` qui + la RLS Postgres.
    """

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def list(self) -> Sequence[Device]:
        result = await self.session.execute(
            select(Device).where(Device.tenant_id == self.tenant_id)
        )
        return result.scalars().all()

    async def add(self, device: Device) -> Device:
        device.tenant_id = self.tenant_id
        self.session.add(device)
        await self.session.flush()
        return device

    async def get(self, device_id: uuid.UUID) -> Device | None:
        result = await self.session.execute(
            select(Device).where(
                Device.id == device_id, Device.tenant_id == self.tenant_id
            )
        )
        return result.scalar_one_or_none()

    async def delete(self, device: Device) -> None:
        await self.session.delete(device)
        await self.session.flush()
