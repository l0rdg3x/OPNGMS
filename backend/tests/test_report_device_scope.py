import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.reporting.service import ReportService


async def _seed_two_devices(factory):
    tid, d1, d2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        for did, name in [(d1, "fw-1"), (d2, "fw-2")]:
            await s.execute(text(
                "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
                "VALUES (:i,:t,:n,'https://x',''::bytea,''::bytea,true,'reachable','{}')"),
                {"i": did, "t": tid, "n": name})
        await s.commit()
    return tid, d1, d2


async def test_device_scoped_report_has_one_section(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, d1, _ = await _seed_two_devices(factory)
    frm = datetime(2026, 6, 1, tzinfo=UTC)
    to = datetime(2026, 6, 8, tzinfo=UTC)
    async with factory() as s:
        html = await ReportService(s, tid).build_html(tenant_name="A", frm=frm, to=to, device_id=d1)
    assert "fw-1" in html and "fw-2" not in html


async def test_fleet_report_has_all_sections(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, _, _ = await _seed_two_devices(factory)
    frm = datetime(2026, 6, 1, tzinfo=UTC)
    to = datetime(2026, 6, 8, tzinfo=UTC)
    async with factory() as s:
        html = await ReportService(s, tid).build_html(tenant_name="A", frm=frm, to=to)
    assert "fw-1" in html and "fw-2" in html
