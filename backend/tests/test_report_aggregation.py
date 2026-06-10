import os
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import make_engine, set_tenant_context
from app.core.db_roles import APP_ROLE, APP_ROLE_PASSWORD
from app.services.reporting.aggregation import ReportAggregator, pick_bucket
from tests.factories import make_tenant


def test_pick_bucket_by_span():
    assert pick_bucket(timedelta(days=1)) == "1 hour"
    assert pick_bucket(timedelta(days=10)) == "6 hours"
    assert pick_bucket(timedelta(days=40)) == "1 day"


async def _seed(db_engine, tenant_id, device_id, names):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:
        for i, name in enumerate(names):
            await s.execute(
                text(
                    "INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip, dst_ip) "
                    "VALUES (:t, :d, 'ids', :k, :tid, :name, '10.0.0.5', '8.8.8.8')"
                ),
                {"t": base + timedelta(minutes=i), "d": device_id, "k": f"k{i}",
                 "tid": tenant_id, "name": name},
            )
        await s.commit()
    return base


async def test_top_and_timeline(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tid = t.id
    did = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id, :t, 'fw1', 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tid},
        )
        await s.commit()
    base = await _seed(db_engine, tid, did, ["ET SCAN", "ET SCAN", "ET POLICY"])

    async with factory() as s:
        agg = ReportAggregator(s, tid)
        devices = await agg.devices()
        assert [d.name for d in devices] == ["fw1"]
        top = await agg.top(field="name", frm=base - timedelta(hours=1), to=base + timedelta(hours=1))
        assert (top[0].value, top[0].count) == ("ET SCAN", 2)
        tl = await agg.timeline(frm=base - timedelta(hours=1), to=base + timedelta(hours=1), bucket="1 hour")
        assert sum(c for _, c in tl) == 3


async def test_top_supports_device_filter_and_blocked_domains(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tid = t.id
    from sqlalchemy import text
    d1, d2 = uuid.uuid4(), uuid.uuid4()
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:
        for did in (d1, d2):
            await s.execute(text("INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                                 "VALUES (:id,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"id": did, "t": tid})
        # DNS events: d1 -> a.com (allowed x2), bad.com (blocked); d2 -> c.com (allowed)
        seed = [(d1,"a.com","allowed"),(d1,"a.com","allowed"),(d1,"bad.com","blocked"),(d2,"c.com","allowed")]
        for i,(did,name,act) in enumerate(seed):
            await s.execute(text("INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip, action) "
                                 "VALUES (:t,:d,'dns',:k,:tid,:n,'10.0.0.1',:a)"),
                            {"t": base, "d": did, "k": f"k{i}", "tid": tid, "n": name, "a": act})
        await s.commit()
    async with factory() as s:
        agg = ReportAggregator(s, tid)
        frm, to = base - timedelta(hours=1), base + timedelta(hours=1)
        # device-scoped Top Sites for d1
        sites = await agg.top(field="name", source="dns", frm=frm, to=to, device_id=d1)
        assert ("a.com", 2) in [(r.value, r.count) for r in sites]
        assert "c.com" not in [r.value for r in sites]   # d2's site excluded
        # Top Blocked (device d1)
        blocked = await agg.top_blocked_domains(frm=frm, to=to, device_id=d1)
        assert [(r.value, r.count) for r in blocked] == [("bad.com", 1)]
        # DNS timeline device-scoped
        tl = await agg.timeline(frm=frm, to=to, bucket="1 hour", source="dns", device_id=d1)
        assert sum(c for _, c in tl) == 3


async def test_bandwidth_timeline_and_totals_reset_safe(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tid = t.id
    from sqlalchemy import text
    did = uuid.uuid4()
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:
        await s.execute(text("INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                             "VALUES (:id,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"id": did, "t": tid})
        # iface wan bytes_in counter samples within one hour bucket: 100, 300, 900 -> delta 800.
        # plus a reset case in the next bucket: 50, 120 -> delta 70 (max-min within bucket).
        samples = [
            (base + timedelta(minutes=0), "iface.bytes_in", "wan", 100.0),
            (base + timedelta(minutes=20), "iface.bytes_in", "wan", 300.0),
            (base + timedelta(minutes=40), "iface.bytes_in", "wan", 900.0),
            (base + timedelta(minutes=65), "iface.bytes_in", "wan", 50.0),   # reset (reboot)
            (base + timedelta(minutes=85), "iface.bytes_in", "wan", 120.0),
            (base + timedelta(minutes=20), "iface.bytes_out", "wan", 10.0),
            (base + timedelta(minutes=40), "iface.bytes_out", "wan", 60.0),  # +50 out
        ]
        for ts, m, lbl, val in samples:
            await s.execute(text("INSERT INTO metrics (time, device_id, metric, label, tenant_id, value) "
                                 "VALUES (:t,:d,:m,:l,:tid,:v)"),
                            {"t": ts, "d": did, "m": m, "l": lbl, "tid": tid, "v": val})
        await s.commit()
    async with factory() as s:
        agg = ReportAggregator(s, tid)
        frm, to = base, base + timedelta(hours=2)
        tl = await agg.bandwidth_timeline(frm=frm, to=to, bucket="1 hour", device_id=did)
        # first bucket: in delta 800 + out delta 50 = 850; second bucket: in delta 70 = 70
        totals_by_bucket = {b: v for b, v in tl}
        assert round(sum(totals_by_bucket.values())) == 920
        ti, to_ = await agg.bandwidth_totals(frm=frm, to=to, device_id=did)
        # totals over the whole range, per-interface max-min reset-clamped is computed per-bucket then summed
        assert ti >= 0 and to_ >= 0


async def test_availability_series_marks_gaps_down(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tid = t.id
    from sqlalchemy import text
    did = uuid.uuid4()
    base = datetime(2026, 6, 9, 0, 0, tzinfo=timezone.utc)
    async with factory() as s:
        await s.execute(text("INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                             "VALUES (:id,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"id": did, "t": tid})
        # cpu.pct present in hour 0 and hour 2, absent in hour 1 and 3 (gaps -> down)
        for h in (0, 2):
            await s.execute(text("INSERT INTO metrics (time, device_id, metric, label, tenant_id, value) "
                                 "VALUES (:t,:d,'cpu.pct','',:tid,5.0)"),
                            {"t": base + timedelta(hours=h, minutes=10), "d": did, "tid": tid})
        await s.commit()
    async with factory() as s:
        agg = ReportAggregator(s, tid)
        series, uptime = await agg.availability_series(frm=base, to=base + timedelta(hours=4), bucket="1 hour", device_id=did)
        ups = [v for _, v in series]
        assert ups == [1, 0, 1, 0]
        assert round(uptime) == 50


async def test_aggregator_is_tenant_isolated_under_rls(db_engine):
    # Seed two tenants + a device + distinct IDS events each, as owner (bypasses RLS).
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    ta, tb = uuid.uuid4(), uuid.uuid4()
    da, db_ = uuid.uuid4(), uuid.uuid4()
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:
        for tid, slug in [(ta, "a"), (tb, "b")]:
            await s.execute(text("INSERT INTO tenants (id, name, slug, status) VALUES (:id, :slug, :slug, 'active')"),
                            {"id": tid, "slug": slug})
        for tid, did, name in [(ta, da, "A-SIG"), (tb, db_, "B-SIG")]:
            await s.execute(
                text("INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                     "VALUES (:id, :t, :n, 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"),
                {"id": did, "t": tid, "n": f"fw-{name}"})
            await s.execute(
                text("INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip, dst_ip) "
                     "VALUES (:t, :d, 'ids', 'k', :tid, :name, '10.0.0.1', '8.8.8.8')"),
                {"t": base, "d": did, "tid": tid, "name": name})
        await s.commit()

    # Connect as the REAL opngms_app role (RLS active), context on tenant A, run the aggregator.
    app_url = make_url(os.environ["TEST_DATABASE_URL"]).set(username=APP_ROLE, password=APP_ROLE_PASSWORD)
    engine = make_engine(app_url.render_as_string(hide_password=False))
    try:
        f2 = async_sessionmaker(engine, expire_on_commit=False)
        async with f2() as s:
            await set_tenant_context(s, ta)
            agg = ReportAggregator(s, ta)
            top = await agg.top(field="name", frm=base - timedelta(hours=1), to=base + timedelta(hours=1))
            names = [r.value for r in top]
            assert "A-SIG" in names
            assert "B-SIG" not in names               # RLS hides tenant B
            assert [d.name for d in await agg.devices()] == ["fw-A-SIG"]
    finally:
        await engine.dispose()
