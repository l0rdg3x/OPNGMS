"""The report path of ReportAggregator.perimeter_top: the per-device report_perimeter toggle filter."""
import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.reporting.aggregation import ReportAggregator


async def _device(s, tid, *, failed_logins=True, firewall_blocks=True):
    did = uuid.uuid4()
    await s.execute(text(
        "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags,report_perimeter) "
        "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}',(:rp)::jsonb)"),
        {"i": did, "t": tid, "rp": json.dumps({"failed_logins": failed_logins, "firewall_blocks": firewall_blocks})})
    return did


async def _rollup(s, did, tid, kind, ip, count, now):
    await s.execute(text(
        "INSERT INTO perimeter_attacker (device_id,kind,src_ip,tenant_id,count,first_seen,last_seen,detail) "
        "VALUES (:d,:k,:ip,:t,:c,:n,:n,'{}'::jsonb)"),
        {"d": did, "k": kind, "ip": ip, "t": tid, "c": count, "n": now})


async def test_report_toggle_excludes_disabled_devices(db_engine, two_tenants):
    ta, _ = two_tenants
    now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        # device A has failed_logins ON; device B has it OFF.
        da = await _device(s, ta, failed_logins=True)
        db = await _device(s, ta, failed_logins=False)
        await _rollup(s, da, ta, "login_failed", "1.1.1.1", 3, now)
        await _rollup(s, db, ta, "login_failed", "2.2.2.2", 9, now)
        await s.commit()

        agg = ReportAggregator(s, ta)
        frm, to = now - timedelta(days=1), now + timedelta(days=1)
        # report path: only device A (toggle on) contributes -> B's higher-count IP is excluded.
        rows = await agg.perimeter_top(kind="login_failed", frm=frm, to=to, geoip=None, limit=10,
                                       report_toggle="failed_logins")
        assert [r.src_ip for r in rows] == ["1.1.1.1"]
        # the API path (no toggle) sees both.
        all_rows = await agg.perimeter_top(kind="login_failed", frm=frm, to=to, geoip=None, limit=10)
        assert {r.src_ip for r in all_rows} == {"1.1.1.1", "2.2.2.2"}
