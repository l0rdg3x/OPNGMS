import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.core.db_roles import APP_ROLE
from app.repositories.metric import MetricRepository


async def _seed(db_engine, tenant_id, device_id):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:  # owner -> bypassa RLS
        for i, v in enumerate((10.0, 20.0, 30.0)):
            await s.execute(
                text(
                    "INSERT INTO metrics (time, device_id, metric, label, tenant_id, value) "
                    "VALUES (:t, :d, 'cpu.load', '', :tid, :v)"
                ),
                {"t": base + timedelta(minutes=i), "d": device_id, "tid": tenant_id, "v": v},
            )
        await s.commit()
    return base


async def test_series_returns_points_in_order(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    device_id = uuid.uuid4()
    base = await _seed(db_engine, tenant_a, device_id)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text(f"SET ROLE {APP_ROLE}"))
        await set_tenant_context(s, tenant_a)
        repo = MetricRepository(s, tenant_a)
        points = await repo.series(
            device_id, "cpu.load", base - timedelta(minutes=1), base + timedelta(minutes=10), None
        )
    assert [p.value for p in points] == [10.0, 20.0, 30.0]
    assert all(p.label == "" for p in points)


async def test_last_returns_latest_per_label(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    device_id = uuid.uuid4()
    await _seed(db_engine, tenant_a, device_id)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text(f"SET ROLE {APP_ROLE}"))
        await set_tenant_context(s, tenant_a)
        repo = MetricRepository(s, tenant_a)
        last = await repo.last(device_id, "cpu.load")
    assert [p.value for p in last] == [30.0]


async def _seed_multi_label(db_engine, tenant_id, device_id):
    """Inserisce 2 timestamp per ciascuna di 2 label (igb0, igb1)."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    rows = [
        ("igb0", 0, 10.0),
        ("igb0", 1, 11.0),  # ultimo per igb0
        ("igb1", 0, 20.0),
        ("igb1", 1, 21.0),  # ultimo per igb1
    ]
    async with factory() as s:  # owner -> bypassa RLS
        for label, minute, value in rows:
            await s.execute(
                text(
                    "INSERT INTO metrics (time, device_id, metric, label, tenant_id, value) "
                    "VALUES (:t, :d, 'if.bytes', :lbl, :tid, :v)"
                ),
                {
                    "t": base + timedelta(minutes=minute),
                    "d": device_id,
                    "lbl": label,
                    "tid": tenant_id,
                    "v": value,
                },
            )
        await s.commit()
    return base


async def test_last_returns_latest_per_distinct_label(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    device_id = uuid.uuid4()
    await _seed_multi_label(db_engine, tenant_a, device_id)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text(f"SET ROLE {APP_ROLE}"))
        await set_tenant_context(s, tenant_a)
        repo = MetricRepository(s, tenant_a)
        last = await repo.last(device_id, "if.bytes")
    # Ordina per label per stabilità: ultimo valore di CIASCUNA label (DISTINCT ON).
    by_label = {p.label: p.value for p in last}
    assert sorted(by_label) == ["igb0", "igb1"]
    assert by_label == {"igb0": 11.0, "igb1": 21.0}


async def test_series_and_last_empty_when_no_data(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    device_id = uuid.uuid4()  # nessun dato per questo device
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text(f"SET ROLE {APP_ROLE}"))
        await set_tenant_context(s, tenant_a)
        repo = MetricRepository(s, tenant_a)
        frm = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
        series = await repo.series(device_id, "cpu.load", frm, frm + timedelta(hours=1), None)
        last = await repo.last(device_id, "cpu.load")
    assert series == []
    assert last == []


async def _seed_four(db_engine, tenant_id, device_id):
    """4 metriche cpu.load (label '') a minuti crescenti con valori 10,20,30,40."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:  # owner -> bypassa RLS
        for i, v in enumerate((10.0, 20.0, 30.0, 40.0)):
            await s.execute(
                text(
                    "INSERT INTO metrics (time, device_id, metric, label, tenant_id, value) "
                    "VALUES (:t, :d, 'cpu.load', '', :tid, :v)"
                ),
                {"t": base + timedelta(minutes=i), "d": device_id, "tid": tenant_id, "v": v},
            )
        await s.commit()
    return base


async def test_series_truncates_to_most_recent_points(db_engine, two_tenants, monkeypatch):
    # Con MAX_POINTS=2 e 4 punti (10,20,30,40), la serie raw deve restituire i 2 PIU'
    # RECENTI (30,40) in ordine crescente, non i 2 piu' vecchi (10,20).
    monkeypatch.setattr("app.repositories.metric.MAX_POINTS", 2)
    tenant_a, _ = two_tenants
    device_id = uuid.uuid4()
    base = await _seed_four(db_engine, tenant_a, device_id)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text(f"SET ROLE {APP_ROLE}"))
        await set_tenant_context(s, tenant_a)
        repo = MetricRepository(s, tenant_a)
        points = await repo.series(
            device_id, "cpu.load", base - timedelta(minutes=1), base + timedelta(minutes=10), None
        )
    assert [p.value for p in points] == [30.0, 40.0]


async def test_series_bucket_downsamples(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    device_id = uuid.uuid4()
    base = await _seed(db_engine, tenant_a, device_id)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text(f"SET ROLE {APP_ROLE}"))
        await set_tenant_context(s, tenant_a)
        repo = MetricRepository(s, tenant_a)
        points = await repo.series(
            device_id, "cpu.load",
            base - timedelta(minutes=1), base + timedelta(minutes=10),
            timedelta(hours=1),  # un bucket -> media (10+20+30)/3 = 20
        )
    assert len(points) == 1
    assert points[0].value == 20.0
