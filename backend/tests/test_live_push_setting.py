from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.app_settings import get_live_push, set_live_push


async def test_get_live_push_defaults_to_env(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        assert await get_live_push(s, env_default=True) is True     # unset -> env default
        assert await get_live_push(s, env_default=False) is False


async def test_set_then_get_live_push(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await set_live_push(s, True)
        await s.commit()
    async with factory() as s:
        assert await get_live_push(s, env_default=False) is True     # DB overrides the env default
        await set_live_push(s, False)
        await s.commit()
    async with factory() as s:
        assert await get_live_push(s, env_default=True) is False
