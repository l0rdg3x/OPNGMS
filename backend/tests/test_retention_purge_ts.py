"""Per-tenant retention on the events + metrics hypertables.

Tenant A (7-day override) is cut at 7d while tenant B (global default) keeps newer rows — proving
per-tenant cutoffs AND cross-tenant isolation in a single DELETE, for both hypertables. Also asserts
the post-migration-0039 reality: the test schema has no native TimescaleDB retention job on
events/metrics (the per-tenant purge below is the sole enforcement)."""
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.models.event import Event
from app.models.metric import Metric
from app.repositories.tenant_retention import TenantRetentionRepository
from app.services.retention import purge_events, purge_metrics


async def _device(s, tenant_id) -> uuid.UUID:
    did = uuid.uuid4()
    await set_tenant_context(s, tenant_id)
    await s.execute(text(
        "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
        "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tenant_id})
    return did


def _event(did, tenant_id, key, when):
    # event_key is part of the composite PK + dedup key — vary it per row.
    return Event(time=when, device_id=did, source="ids", event_key=key, tenant_id=tenant_id)


def _metric(did, tenant_id, label, when):
    # (time, device_id, metric, label) is the composite PK — vary `label` per row.
    return Metric(time=when, device_id=did, metric="cpu", label=label, tenant_id=tenant_id, value=1.0)


async def test_no_native_retention_job_on_hypertables(db_engine):
    """Post-0039 reality: no native TimescaleDB retention policy on events/metrics (the per-tenant
    purge is the sole enforcement). The test schema is built without add_retention_policy, matching
    the state migration 0039 leaves behind."""
    async with db_engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT hypertable_name FROM timescaledb_information.jobs "
            "WHERE proc_name = 'policy_retention' AND hypertable_name IN ('events', 'metrics')"
        ))).scalars().all()
    assert list(rows) == []


async def test_events_per_tenant_cutoffs_and_isolation(two_tenants, db_engine):
    ta, tb = two_tenants
    now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async with factory() as s:
        da = await _device(s, ta)
        await set_tenant_context(s, tb)
        db = await _device(s, tb)
        # Tenant A: 7-day override; tenant B: no override -> global default (90 in test, gd=90 below).
        await TenantRetentionRepository(s, ta).upsert({"events": 7})
        # A rows: 10 days (older than 7d -> purged), 3 days (kept).
        s.add(_event(da, ta, "a-old", now - timedelta(days=10)))
        s.add(_event(da, ta, "a-new", now - timedelta(days=3)))
        # B rows (default): 100 days (older than 90d -> purged), 20 days (kept; WOULD be cut by A's 7d
        # cutoff -> proves the cutoff is per-tenant, not global).
        s.add(_event(db, tb, "b-old", now - timedelta(days=100)))
        s.add(_event(db, tb, "b-new", now - timedelta(days=20)))
        await s.commit()

        deleted = await purge_events(s, now, global_default=90)
        await s.commit()

        remaining = {r.event_key for r in (await s.execute(select(Event))).scalars().all()}

    assert deleted == 2
    assert remaining == {"a-new", "b-new"}


async def test_metrics_per_tenant_cutoffs_and_isolation(two_tenants, db_engine):
    ta, tb = two_tenants
    now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async with factory() as s:
        da = await _device(s, ta)
        await set_tenant_context(s, tb)
        db = await _device(s, tb)
        # Tenant A: 7-day override; tenant B: no override -> global default (30 below).
        await TenantRetentionRepository(s, ta).upsert({"metrics": 7})
        s.add(_metric(da, ta, "a-old", now - timedelta(days=10)))  # >7d -> purged
        s.add(_metric(da, ta, "a-new", now - timedelta(days=3)))   # kept
        s.add(_metric(db, tb, "b-old", now - timedelta(days=40)))  # >30d -> purged
        s.add(_metric(db, tb, "b-new", now - timedelta(days=20)))  # kept (within 30d, not cut at A's 7d)
        await s.commit()

        deleted = await purge_metrics(s, now, global_default=30)
        await s.commit()

        remaining = {r.label for r in (await s.execute(select(Metric))).scalars().all()}

    assert deleted == 2
    assert remaining == {"a-new", "b-new"}
