"""Per-tenant perimeter purge: tenant A (7-day override) is cut at 7d while tenant B (global default
30d) keeps newer rows — proving per-tenant cutoffs AND cross-tenant isolation in one DELETE."""
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.models.perimeter_attacker import PerimeterAttacker
from app.repositories.tenant_retention import TenantRetentionRepository
from app.services.perimeter import purge_perimeter


async def _device(s, tenant_id) -> uuid.UUID:
    did = uuid.uuid4()
    await set_tenant_context(s, tenant_id)
    await s.execute(text(
        "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
        "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tenant_id})
    return did


def _row(did, tenant_id, ip, last_seen):
    return PerimeterAttacker(device_id=did, kind="firewall_block", src_ip=ip, tenant_id=tenant_id,
                             count=1, first_seen=last_seen, last_seen=last_seen)


async def test_per_tenant_cutoffs_and_isolation(two_tenants, db_engine):
    ta, tb = two_tenants
    now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async with factory() as s:
        da = await _device(s, ta)
        db = await _device(s, tb)
        # Tenant A: 7-day override.
        await TenantRetentionRepository(s, ta).upsert({"perimeter": 7})
        # A rows: one at 10 days (older than 7d override -> purged), one at 3 days (kept).
        s.add(_row(da, ta, "10.0.0.1", now - timedelta(days=10)))
        s.add(_row(da, ta, "10.0.0.2", now - timedelta(days=3)))
        # B rows (no override -> global default 30): one at 40 days (purged), one at 20 days (kept,
        # though it WOULD be purged under A's 7-day cutoff -> proves the cutoff is per-tenant).
        s.add(_row(db, tb, "10.0.1.1", now - timedelta(days=40)))
        s.add(_row(db, tb, "10.0.1.2", now - timedelta(days=20)))
        await s.commit()

        deleted = await purge_perimeter(s, now, global_default=30)
        await s.commit()

        remaining = {r.src_ip for r in (await s.execute(select(PerimeterAttacker))).scalars().all()}

    assert deleted == 2
    # A: 10d row gone (>7d), 3d row kept. B: 40d gone (>30d), 20d kept (within 30d, NOT cut at A's 7d).
    assert remaining == {"10.0.0.2", "10.0.1.2"}
