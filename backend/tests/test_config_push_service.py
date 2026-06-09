import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.config_change import ConfigChange
from app.services.config_push import create_change, preview_change


async def _device_with_snapshot(db_engine, tenant_id, canon="h1"):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
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
                "INSERT INTO config_snapshots (id, tenant_id, device_id, canonical_hash, content_enc) "
                "VALUES (:id, :t, :d, :h, '\\x00'::bytea)"
            ),
            {"id": uuid.uuid4(), "t": tenant_id, "d": did, "h": canon},
        )
        await s.commit()
    return did


async def test_create_change_captures_baseline_hash(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device_with_snapshot(db_engine, tenant_a, canon="base-h")
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ch = await create_change(
            s, tenant_id=tenant_a, device_id=did, created_by=uuid.uuid4(),
            kind="alias", operation="set", target="myalias", payload={"name": "myalias", "content": ["1.2.3.4"]},
        )
        await s.commit()
        cid = ch.id
    async with factory() as s:
        row = await s.get(ConfigChange, cid)
    assert row.status == "draft"
    assert row.baseline_hash == "base-h"   # captured from the latest snapshot
    assert row.payload["name"] == "myalias"


def test_preview_is_secret_safe_summary():
    ch = ConfigChange(
        tenant_id=uuid.uuid4(), device_id=uuid.uuid4(), created_by=uuid.uuid4(),
        kind="alias", operation="set", target="myalias",
        payload={"name": "myalias", "content": ["1.2.3.4"]}, baseline_hash="h",
    )
    p = preview_change(ch)
    assert p["operation"] == "set" and p["kind"] == "alias" and p["target"] == "myalias"
    assert p["new"] == {"name": "myalias", "content": ["1.2.3.4"]}
