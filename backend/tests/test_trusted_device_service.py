from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.trusted_device import TrustedDevice
from app.services.trusted_device import TrustedDeviceService
from tests.factories import make_user


async def _user(db_engine, email="svc@x.io"):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        u = await make_user(s, email=email, password="pw12345-secure")
        await s.commit()
        return u.id


async def test_create_then_find_valid(db_engine):
    uid = await _user(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        svc = TrustedDeviceService(s)
        row, raw = await svc.create_for_user(uid, days=30, user_agent="UA", ip="1.2.3.4")
        await s.commit()
        assert raw and row.token_hash != raw  # only the hash is stored
        found = await svc.find_valid(uid, raw)
        assert found is not None and found.id == row.id


async def test_find_valid_rejects_wrong_user(db_engine):
    uid = await _user(db_engine, "a@x.io")
    other = await _user(db_engine, "b@x.io")
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        svc = TrustedDeviceService(s)
        _, raw = await svc.create_for_user(uid, days=30, user_agent=None, ip=None)
        await s.commit()
        assert await svc.find_valid(other, raw) is None  # token belongs to uid, not other


async def test_find_valid_rejects_expired(db_engine):
    uid = await _user(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        svc = TrustedDeviceService(s)
        row, raw = await svc.create_for_user(uid, days=30, user_agent=None, ip=None)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await s.commit()
        assert await svc.find_valid(uid, raw) is None


async def test_find_valid_rejects_unknown_garbage_and_empty(db_engine):
    uid = await _user(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        svc = TrustedDeviceService(s)
        assert await svc.find_valid(uid, "not-a-real-token") is None
        assert await svc.find_valid(uid, "") is None  # empty token rejected before any DB lookup


async def test_touch_updates_last_used(db_engine):
    uid = await _user(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        svc = TrustedDeviceService(s)
        row, _ = await svc.create_for_user(uid, days=30, user_agent=None, ip=None)
        row.last_used_at = datetime.now(UTC) - timedelta(days=1)
        await s.commit()
        before = row.last_used_at
        await svc.touch(row)
        await s.commit()
        assert row.last_used_at > before


async def test_list_and_revoke(db_engine):
    uid = await _user(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        svc = TrustedDeviceService(s)
        r1, _ = await svc.create_for_user(uid, days=30, user_agent="A", ip=None)
        r2, _ = await svc.create_for_user(uid, days=30, user_agent="B", ip=None)
        await s.commit()
        rows = await svc.list_for_user(uid)
        assert {r.id for r in rows} == {r1.id, r2.id}
        assert await svc.revoke(r1.id, uid) is True
        assert await svc.revoke(r1.id, uid) is False  # already gone
        await s.commit()
        assert {r.id for r in await svc.list_for_user(uid)} == {r2.id}


async def test_revoke_scoped_to_owner(db_engine):
    uid = await _user(db_engine, "a@x.io")
    other = await _user(db_engine, "b@x.io")
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        svc = TrustedDeviceService(s)
        r1, _ = await svc.create_for_user(uid, days=30, user_agent=None, ip=None)
        await s.commit()
        assert await svc.revoke(r1.id, other) is False  # not other's device
        await s.commit()
        assert len(await svc.list_for_user(uid)) == 1


async def test_revoke_all_and_purge_expired(db_engine):
    uid = await _user(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        svc = TrustedDeviceService(s)
        live, _ = await svc.create_for_user(uid, days=30, user_agent=None, ip=None)
        dead, _ = await svc.create_for_user(uid, days=30, user_agent=None, ip=None)
        dead.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await s.commit()
        assert await svc.purge_expired(datetime.now(UTC)) == 1
        await s.commit()
        assert {r.id for r in await svc.list_for_user(uid)} == {live.id}
        n = await svc.revoke_all(uid)
        await s.commit()
        assert n == 1
        assert (await s.execute(select(TrustedDevice).where(TrustedDevice.user_id == uid))).first() is None
