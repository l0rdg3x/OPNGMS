from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.app_settings import get_mfa_policy, set_mfa_policy


async def test_mfa_policy_defaults_off_and_roundtrips(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        assert await get_mfa_policy(s) == "off"
        await set_mfa_policy(s, "privileged")
        await s.commit()
    async with factory() as s:
        assert await get_mfa_policy(s) == "privileged"
