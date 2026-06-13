import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.factories import make_tenant


async def _login_superadmin(api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tid = t.id
    await api_client.post(
        "/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345-secure"})
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345-secure"})
    return tid


async def test_get_device_plugins_returns_stored_telemetry(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    did = uuid.uuid4()
    plugins_json = (
        '[{"name":"os-wireguard","installed":true,"version":"2.6","locked":false},'
        '{"name":"os-acme-client","installed":false,"version":"4.16","locked":false}]'
    )
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, "
                "verify_tls, status, tags, installed_plugins) VALUES "
                "(:id,:t,'fw','https://fw',''::bytea,''::bytea,true,'reachable','{}',"
                "CAST(:plugins AS jsonb))"
            ),
            {"id": did, "t": tid, "plugins": plugins_json},
        )
        await s.commit()
    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/plugins")
    assert r.status_code == 200
    body = {p["name"]: p for p in r.json()}
    assert set(body) == {"os-wireguard", "os-acme-client"}
    assert body["os-wireguard"]["installed"] is True
    assert body["os-acme-client"]["version"] == "4.16"


async def test_get_device_plugins_404_for_unknown_device(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    r = await api_client.get(f"/api/tenants/{tid}/devices/{uuid.uuid4()}/plugins")
    assert r.status_code == 404
