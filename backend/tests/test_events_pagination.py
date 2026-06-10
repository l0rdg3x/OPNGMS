import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.repositories.event import EventRepository


@pytest.fixture
async def factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def _seed(factory, tid, did, n):
    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'T','t','active')"), {"i": tid})
        await s.execute(
            text("INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags)"
                 " VALUES (:i,:t,'d','https://d',''::bytea,''::bytea,true,'unverified','{}')"),
            {"i": did, "t": tid},
        )
        for k in range(n):
            await s.execute(
                text("INSERT INTO events (time,device_id,source,event_key,tenant_id,name)"
                     " VALUES (:tm,:d,'suricata',:ek,:t,:nm)"),
                {"tm": base + timedelta(minutes=k), "d": did, "ek": f"k{k}", "t": tid, "nm": f"e{k}"},
            )
        await s.commit()


async def test_keyset_pages_cover_all_rows_without_overlap(factory):
    tid, did = uuid.uuid4(), uuid.uuid4()
    await _seed(factory, tid, did, 5)
    async with factory() as s:
        repo = EventRepository(s, tid)
        page1, c1 = await repo.list_page(source=None, device_id=None, frm=None, to=None, after=None, limit=2)
        page2, c2 = await repo.list_page(source=None, device_id=None, frm=None, to=None, after=c1, limit=2)
        page3, c3 = await repo.list_page(source=None, device_id=None, frm=None, to=None, after=c2, limit=2)
    names = [e.name for e in page1 + page2 + page3]
    assert names == ["e4", "e3", "e2", "e1", "e0"]  # time DESC, no overlap/gap
    assert c1 is not None and c2 is not None
    assert c3 is None  # last page (fewer than limit) -> no next cursor


async def test_keyset_handles_equal_timestamps(factory):
    # Rows sharing the SAME (time, device, source) and differing only on event_key exercise the
    # tiebreaker chain of the cursor. Paging in size-2 windows must still cover every row once.
    tid, did = uuid.uuid4(), uuid.uuid4()
    ts = datetime(2026, 6, 1, tzinfo=timezone.utc)
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'T','t','active')"), {"i": tid})
        await s.execute(
            text("INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags)"
                 " VALUES (:i,:t,'d','https://d',''::bytea,''::bytea,true,'unverified','{}')"),
            {"i": did, "t": tid},
        )
        for k in range(5):  # all share the identical timestamp
            await s.execute(
                text("INSERT INTO events (time,device_id,source,event_key,tenant_id,name)"
                     " VALUES (:tm,:d,'suricata',:ek,:t,:nm)"),
                {"tm": ts, "d": did, "ek": f"k{k}", "t": tid, "nm": f"e{k}"},
            )
        await s.commit()
    collected, cursor = [], None
    async with factory() as s:
        repo = EventRepository(s, tid)
        for _ in range(5):  # bounded loop; should terminate well before
            page, cursor = await repo.list_page(
                source=None, device_id=None, frm=None, to=None, after=cursor, limit=2
            )
            collected += [e.name for e in page]
            if cursor is None:
                break
    # Every row exactly once, no overlap/gap, ordered by event_key DESC (the only discriminator).
    assert collected == ["e4", "e3", "e2", "e1", "e0"]
    assert cursor is None
