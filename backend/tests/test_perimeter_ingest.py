import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.device import Device
from app.models.ingest_cursor import IngestCursor
from app.models.perimeter_attacker import PerimeterAttacker
from app.services.perimeter import ingest_perimeter, purge_perimeter


class FakeClient:
    """Returns ALREADY-PARSED capability rows (the capability does the parsing upstream)."""

    def __init__(self, fw=None, au=None):
        self._fw, self._au = fw or [], au or []

    async def get_firewall_blocks(self, since=None):
        return self._fw

    async def get_auth_failures(self, since=None):
        return self._au


async def _device(db_engine, tenant_id) -> uuid.UUID:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    async with factory() as s:
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tenant_id})
        await s.commit()
    return did


def _fw(ts, ip, port, digest):
    return {"time": ts, "src_ip": ip, "name": str(port), "event_key": digest,
            "attributes": {"dstport": str(port), "interface": "igb0"}}


async def test_ingest_perimeter_rolls_up_by_ip(db_engine, two_tenants):
    ta, _ = two_tenants
    did = await _device(db_engine, ta)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        dev = await s.get(Device, did)
        fw = [_fw(datetime(2026, 6, 14, 10, 0, tzinfo=UTC), "1.1.1.1", 23, "d1"),
              _fw(datetime(2026, 6, 14, 10, 1, tzinfo=UTC), "1.1.1.1", 80, "d2")]
        n = await ingest_perimeter(s, dev, FakeClient(fw=fw), now=datetime.now(UTC))
        await s.commit()
        row = (await s.execute(select(PerimeterAttacker).where(PerimeterAttacker.src_ip == "1.1.1.1"))).scalar_one()
    assert n == 2
    assert row.kind == "firewall_block" and row.count == 2 and row.tenant_id == ta
    assert set(row.detail.get("top_ports", [])) >= {"23", "80"}
    # the cursor advanced to the newest row time
    async with factory() as s:
        cur = await s.get(IngestCursor, (did, "firewall_block"))
    assert cur is not None and cur.last_time == datetime(2026, 6, 14, 10, 1, tzinfo=UTC)


async def test_ingest_perimeter_second_run_increments_and_skips_seen(db_engine, two_tenants):
    ta, _ = two_tenants
    did = await _device(db_engine, ta)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        dev = await s.get(Device, did)
        await ingest_perimeter(s, dev, FakeClient(fw=[_fw(datetime(2026, 6, 14, 10, 0, tzinfo=UTC), "2.2.2.2", 22, "d1")]), now=datetime.now(UTC))
        await s.commit()
    # second poll returns the old row again + a new one; only the new one counts (cursor filter)
    async with factory() as s:
        dev = await s.get(Device, did)
        fw = [_fw(datetime(2026, 6, 14, 10, 0, tzinfo=UTC), "2.2.2.2", 22, "d1"),
              _fw(datetime(2026, 6, 14, 10, 5, tzinfo=UTC), "2.2.2.2", 443, "d2")]
        n = await ingest_perimeter(s, dev, FakeClient(fw=fw), now=datetime.now(UTC))
        await s.commit()
        row = (await s.execute(select(PerimeterAttacker).where(PerimeterAttacker.src_ip == "2.2.2.2"))).scalar_one()
    assert n == 1 and row.count == 2  # 1 + 1, the already-seen row excluded by the cursor


async def test_purge_perimeter_drops_stale_rows(db_engine, two_tenants):
    ta, _ = two_tenants
    did = await _device(db_engine, ta)
    now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        s.add(PerimeterAttacker(device_id=did, kind="firewall_block", src_ip="9.9.9.9", tenant_id=ta,
                                count=1, first_seen=now - timedelta(days=90), last_seen=now - timedelta(days=90)))
        s.add(PerimeterAttacker(device_id=did, kind="firewall_block", src_ip="8.8.8.8", tenant_id=ta,
                                count=1, first_seen=now, last_seen=now))
        await s.commit()
        deleted = await purge_perimeter(s, now=now)
        await s.commit()
        remaining = (await s.execute(select(PerimeterAttacker.src_ip))).scalars().all()
    assert deleted == 1 and remaining == ["8.8.8.8"]
