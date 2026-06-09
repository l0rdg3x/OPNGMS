from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.audit import AuditLog


async def _audit_actions(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        rows = (await s.execute(select(AuditLog))).scalars().all()
        return [r.action for r in rows]


async def test_login_and_logout_are_audited(api_client, db_engine):
    await api_client.post(
        "/api/setup", json={"email": "a@x.io", "name": "A", "password": "pw12345"}
    )
    await api_client.post("/api/login", json={"email": "a@x.io", "password": "pw12345"})
    actions = await _audit_actions(db_engine)
    assert "auth.login" in actions
    await api_client.post("/api/logout", headers={"X-OPNGMS-CSRF": "1"})
    actions = await _audit_actions(db_engine)
    assert "auth.logout" in actions
