from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.trusted_device import TrustedDevice
from tests.factories import make_user


async def test_trusted_device_row_roundtrip(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        u = await make_user(s, email="td@x.io", password="pw12345-secure")
        now = datetime.now(UTC)
        s.add(TrustedDevice(
            user_id=u.id, token_hash="a" * 64, user_agent="UA", ip="1.2.3.4",
            expires_at=now + timedelta(days=30),
        ))
        await s.commit()
        row = (await s.execute(select(TrustedDevice).where(TrustedDevice.user_id == u.id))).scalar_one()
        assert row.token_hash == "a" * 64
        assert row.user_agent == "UA"
        assert row.ip == "1.2.3.4"
        assert row.created_at is not None
        assert row.last_used_at is not None
        assert row.expires_at > now
