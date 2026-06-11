import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.config_change import ConfigChange
from app.services.config_push import apply_change


class FakeClient:
    def __init__(self, xml):
        self._xml = xml
        self.apply_called = False

    async def get_config_backup(self):
        return self._xml

    async def apply_alias(self, operation, payload, *, dry_run=True):
        self.apply_called = True
        return {"dry_run": dry_run, "operation": operation}


XML = "<opnsense><system><hostname>fw1</hostname></system></opnsense>"
# canonical_hash(XML) computed by the service; the test reads it back via the same fn.


async def _scheduled_change(db_engine, tenant_id, baseline_hash, status="scheduled") -> uuid.UUID:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    cid = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id, :t, 'fw', 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tenant_id},
        )
        await s.execute(
            text(
                "INSERT INTO config_changes (id, tenant_id, device_id, created_by, kind, operation, target, payload, baseline_hash, status) "
                "VALUES (:id, :t, :d, :u, 'alias', 'set', 'a', '{}'::jsonb, :h, :st)"
            ),
            {"id": cid, "t": tenant_id, "d": did, "u": uuid.uuid4(), "h": baseline_hash, "st": status},
        )
        await s.commit()
    return cid


async def test_apply_matching_hash_applies_dry_run(db_engine, two_tenants):
    from app.services.config_diff import canonical_hash

    tenant_a, _ = two_tenants
    cid = await _scheduled_change(db_engine, tenant_a, baseline_hash=canonical_hash(XML))
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ch = await s.get(ConfigChange, cid)
        client = FakeClient(XML)
        status = await apply_change(s, ch, client, now=datetime.now(timezone.utc))
        await s.commit()
    assert status == "applied"
    assert client.apply_called is True
    async with factory() as s:
        ch = await s.get(ConfigChange, cid)
    assert ch.status == "applied" and ch.result.get("dry_run") is True


async def test_apply_stale_hash_conflicts(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    cid = await _scheduled_change(db_engine, tenant_a, baseline_hash="STALE")  # != hash(XML)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ch = await s.get(ConfigChange, cid)
        client = FakeClient(XML)
        status = await apply_change(s, ch, client, now=datetime.now(timezone.utc))
        await s.commit()
    assert status == "conflict"
    # CRITICAL: a stale config must NOT be applied (no clobber).
    assert client.apply_called is False
    async with factory() as s:
        ch = await s.get(ConfigChange, cid)
    assert ch.status == "conflict"


async def test_apply_non_scheduled_is_noop(db_engine, two_tenants):
    from app.services.config_diff import canonical_hash

    tenant_a, _ = two_tenants
    cid = await _scheduled_change(
        db_engine, tenant_a, baseline_hash=canonical_hash(XML), status="cancelled"
    )
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ch = await s.get(ConfigChange, cid)
        client = FakeClient(XML)
        status = await apply_change(s, ch, client, now=datetime.now(timezone.utc))
        await s.commit()
    assert status == "cancelled"
    assert client.apply_called is False
    async with factory() as s:
        ch = await s.get(ConfigChange, cid)
    assert ch.status == "cancelled"


async def test_apply_live_applies_real_and_snapshots(db_engine, two_tenants, monkeypatch):
    from types import SimpleNamespace

    from app.models.config_snapshot import ConfigSnapshot
    from app.services.config_diff import canonical_hash

    monkeypatch.setattr(
        "app.services.config_push.get_settings",
        lambda: SimpleNamespace(live_push_enabled=True),
    )
    tenant_a, _ = two_tenants
    cid = await _scheduled_change(db_engine, tenant_a, baseline_hash=canonical_hash(XML))
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ch = await s.get(ConfigChange, cid)
        client = FakeClient(XML)
        status = await apply_change(s, ch, client, now=datetime.now(timezone.utc))
        await s.commit()
    assert status == "applied"
    assert client.apply_called is True
    async with factory() as s:
        ch = await s.get(ConfigChange, cid)
        assert ch.result.get("dry_run") is False          # real apply
        assert ch.pre_apply_snapshot_id is not None        # rollback point captured
        snap = await s.get(ConfigSnapshot, ch.pre_apply_snapshot_id)
        assert snap is not None and snap.device_id == ch.device_id
        # the rollback snapshot records the device firmware version (here "" — the test device
        # has no firmware_version set, but the field is now populated from the device, not hardcoded)
        assert snap.opnsense_version == ""
