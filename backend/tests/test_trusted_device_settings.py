from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.deps import TRUSTED_DEVICE_COOKIE
from app.services.app_settings import get_trusted_device_enabled, set_trusted_device_enabled
from app.services.runtime_settings import runtime_defaults


def test_cookie_constant():
    assert TRUSTED_DEVICE_COOKIE == "opngms_trusted_device"


def test_trusted_device_days_default_is_30():
    assert runtime_defaults()["trusted_device_days"] == 30


async def test_toggle_default_on_then_override(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        assert await get_trusted_device_enabled(s, env_default=True) is True  # no row -> env default
        await set_trusted_device_enabled(s, False)
        await s.commit()
        assert await get_trusted_device_enabled(s, env_default=True) is False
        await set_trusted_device_enabled(s, True)
        await s.commit()
        assert await get_trusted_device_enabled(s, env_default=True) is True
