import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.audit import AuditLog
from app.services.audit import AuditService
from tests.factories import make_user


async def test_record_writes_audit_row(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        user = await make_user(s, email="audit@test.com")
        actor = user.id
        await s.commit()
    async with factory() as s:
        await AuditService(s).record(
            actor_user_id=actor,
            tenant_id=None,
            action="tenant.create",
            target_type="tenant",
            target_id="abc",
            ip="1.2.3.4",
            details={"name": "X"},
        )
        await s.commit()
    async with factory() as s:
        rows = (await s.execute(select(AuditLog))).scalars().all()
        assert any(
            r.action == "tenant.create" and r.actor_user_id == actor and r.details == {"name": "X"}
            for r in rows
        )
