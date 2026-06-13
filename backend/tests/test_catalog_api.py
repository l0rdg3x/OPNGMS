import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services import catalog_provider
from tests.conftest import csrf_headers
from tests.factories import make_membership, make_tenant, make_user

_CATALOG = {
    "edition": "community", "version": "26.1.8",
    "models": {
        "unbound": {
            "id": "unbound", "model_root": "unbound",
            "endpoints": {"get": "unbound/settings/get", "set": "unbound/settings/set",
                          "reconfigure": "unbound/service/reconfigure"},
            "fields": [{"path": "general.enabled", "type": "bool"}],
            "grids": [{"path": "hosts",
                       "endpoints": {"add": "unbound/settings/addHosts",
                                     "set": "unbound/settings/setHosts",
                                     "del": "unbound/settings/delHosts"},
                       "fields": [{"path": "hostname", "type": "string"}]}],
        },
        "interfaces": {"id": "interfaces", "model_root": "interfaces",
                       "endpoints": {"set": "interfaces/settings/set",
                                     "reconfigure": "interfaces/service/reconfigure"},
                       "fields": [{"path": "lan.if", "type": "string"}], "grids": []},
    },
}


async def _fake_get_catalog(session, edition, version, **kw):
    return _CATALOG


async def _seed(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        admin = await make_user(s, email="ta@x.io", password="pw12345-secure")
        await make_membership(s, user_id=admin.id, tenant_id=t.id, role="tenant_admin")
        await s.commit()
        return t.id


async def _device(db_engine, tid, edition="community", version="26.1.8"):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    async with factory() as s:
        await s.execute(text(
            "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, "
            "verify_tls, status, tags, edition, firmware_version) "
            "VALUES (:id,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}',:e,:v)"),
            {"id": did, "t": tid, "e": edition, "v": version})
        await s.commit()
    return did


async def _login(api_client, email="ta@x.io"):
    await api_client.post("/api/login", json={"email": email, "password": "pw12345-secure"})


async def test_create_catalog_change_scalar(api_client, db_engine, monkeypatch):
    monkeypatch.setattr(catalog_provider, "get_catalog", _fake_get_catalog)
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _login(api_client)
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/catalog/changes",
        json={"model_id": "unbound", "scalars": {"general.enabled": "1"}},
        headers=csrf_headers(api_client))
    assert r.status_code == 201
    body = r.json()
    assert body["kind"] == "catalog_setting"
    assert body["status"] == "draft"
    assert "payload" not in body  # internals hidden


async def test_create_catalog_change_unknown_model_422(api_client, db_engine, monkeypatch):
    monkeypatch.setattr(catalog_provider, "get_catalog", _fake_get_catalog)
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _login(api_client)
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/catalog/changes",
        json={"model_id": "does-not-exist", "scalars": {"a": "1"}},
        headers=csrf_headers(api_client))
    assert r.status_code == 422


async def test_create_catalog_change_denylisted_model_422(api_client, db_engine, monkeypatch):
    monkeypatch.setattr(catalog_provider, "get_catalog", _fake_get_catalog)
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _login(api_client)
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/catalog/changes",
        json={"model_id": "interfaces", "scalars": {"lan.if": "em0"}},
        headers=csrf_headers(api_client))
    assert r.status_code == 422


async def test_create_catalog_change_unknown_scalar_field_422(api_client, db_engine, monkeypatch):
    monkeypatch.setattr(catalog_provider, "get_catalog", _fake_get_catalog)
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _login(api_client)
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/catalog/changes",
        json={"model_id": "unbound", "scalars": {"general.nope": "1"}},
        headers=csrf_headers(api_client))
    assert r.status_code == 422


async def test_create_catalog_change_no_catalog_404(api_client, db_engine, monkeypatch):
    async def _none(session, edition, version, **kw):
        return None
    monkeypatch.setattr(catalog_provider, "get_catalog", _none)
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _login(api_client)
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/catalog/changes",
        json={"model_id": "unbound", "scalars": {"general.enabled": "1"}},
        headers=csrf_headers(api_client))
    assert r.status_code == 404


async def test_read_catalog_returns_models_and_resolved(api_client, db_engine, monkeypatch):
    monkeypatch.setattr(catalog_provider, "get_catalog", _fake_get_catalog)
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid, edition="business", version="26.4")
    await _login(api_client)
    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/catalog",
        headers=csrf_headers(api_client))
    assert r.status_code == 200
    body = r.json()
    assert body["edition"] == "business" and body["version"] == "26.4"
    # resolved_* come from the served catalog (Community shared core)
    assert body["resolved_edition"] == "community" and body["resolved_version"] == "26.1.8"
    assert body["models"]["interfaces"]["read_only"] is True
    assert body["models"]["unbound"]["read_only"] is False


async def test_read_catalog_404_when_unavailable(api_client, db_engine, monkeypatch):
    async def _none(session, edition, version, **kw):
        return None
    monkeypatch.setattr(catalog_provider, "get_catalog", _none)
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _login(api_client)
    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/catalog", headers=csrf_headers(api_client))
    assert r.status_code == 404
