import uuid
from datetime import timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.device import Device
from app.services.alerting import raise_config_audit_alerts


def _row(name="root", severity="medium"):
    return {"name": name, "severity": severity}


async def _device(db_engine, tenant_id) -> uuid.UUID:
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
        await s.commit()
    return did


async def test_drift_change_opens_one_deduped_alert(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        n1 = await raise_config_audit_alerts(s, device, [_row(), _row()])   # same actor twice -> 1
        await s.commit()
    assert n1 == 1
    async with factory() as s:
        device = await s.get(Device, did)
        n2 = await raise_config_audit_alerts(s, device, [_row()])           # already open -> 0
        await s.commit()
    assert n2 == 0
    async with factory() as s:
        cnt = (await s.execute(
            text("SELECT count(*) FROM alerts WHERE type='config_audit'"))).scalar_one()
    assert cnt == 1


async def test_api_change_never_alerts(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        n = await raise_config_audit_alerts(s, device, [_row(severity="info")])
        await s.commit()
    assert n == 0
