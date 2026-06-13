import uuid

import respx
from httpx import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import crypto
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
            "fields": [{"path": "general.enabled", "type": "bool"},
                       {"path": "general.outgoing", "type": "ref"}],
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
    "menu": [{"id": "Services", "label": "Services", "order": 50, "children": [
        {"id": "Unbound", "label": "Unbound DNS", "order": 0, "children": [
            {"id": "General", "label": "General", "order": 10,
             "url": "/ui/unbound/general", "model_id": "unbound"}]}]}],
}


async def _fake_get_catalog(session, edition, version, **kw):
    return _CATALOG


async def _no_catalog(session, edition, version, **kw):
    return None


async def _seed(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        admin = await make_user(s, email="ta@x.io", password="pw12345-secure")
        await make_membership(s, user_id=admin.id, tenant_id=t.id, role="tenant_admin")
        await s.commit()
        return t.id


async def _device(db_engine, tid, edition="community", version="26.1.8", base_url="https://x"):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    # REAL encrypted credentials (decryptable server-side): the live-model endpoint decrypts
    # them to build the connector, so empty tokens would fail to decrypt before any live read.
    key_enc = crypto.encrypt("apikey")
    secret_enc = crypto.encrypt("apisecret")
    async with factory() as s:
        await s.execute(text(
            "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, "
            "verify_tls, status, tags, edition, firmware_version) "
            "VALUES (:id,:t,'fw',:url,:k,:sec,true,'reachable','{}',:e,:v)"),
            {"id": did, "t": tid, "url": base_url, "k": key_enc, "sec": secret_enc,
             "e": edition, "v": version})
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
    monkeypatch.setattr(catalog_provider, "get_plugins_catalog", _no_catalog)  # no network in the fallback
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


async def test_create_catalog_change_no_catalog_422(api_client, db_engine, monkeypatch):
    # With neither the core nor the plugins catalog resolving the model, the change endpoint reports
    # it as an unknown model (422) — the core->plugins fallback collapses "no catalog" into "no model".
    async def _none(session, edition, version, **kw):
        return None
    monkeypatch.setattr(catalog_provider, "get_catalog", _none)
    monkeypatch.setattr(catalog_provider, "get_plugins_catalog", _none)
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _login(api_client)
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/catalog/changes",
        json={"model_id": "unbound", "scalars": {"general.enabled": "1"}},
        headers=csrf_headers(api_client))
    assert r.status_code == 422


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
    # the OPNsense-like menu tree (3b) must be forwarded to the editor
    assert body["menu"][0]["id"] == "Services"
    assert body["menu"][0]["children"][0]["children"][0]["model_id"] == "unbound"


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


# A literal public IP base_url (TEST-NET-3): it needs no DNS and passes the SSRF guard, so the
# connector actually issues the request and respx can intercept it (a hostname like 'x' would be
# rejected at DNS resolution by the SSRF guard before any live read — see test_opnsense_client).
_DEV_URL = "https://203.0.113.10"


def _live_get_payload():
    # what the device returns for unbound/settings/get
    return {"unbound": {
        "general": {"enabled": "1", "port": "53"},
        "hosts": {"ab-12": {"hostname": "web", "server": "10.0.0.10"}},
    }}


@respx.mock
async def test_read_model_merges_live_values(api_client, db_engine, monkeypatch):
    monkeypatch.setattr(catalog_provider, "get_catalog", _fake_get_catalog)
    # OpnsenseClient.get_setting issues a GET; the get(...) mock is the one that matters.
    respx.get(f"{_DEV_URL}/api/unbound/settings/get").mock(
        return_value=Response(200, json=_live_get_payload()))
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid, base_url=_DEV_URL)
    await _login(api_client)
    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/catalog/models/unbound",
        headers=csrf_headers(api_client))
    assert r.status_code == 200
    body = r.json()
    assert body["reachable"] is True and body["read_only"] is False
    assert body["values"]["general.enabled"] == "1"
    assert body["grids"]["hosts"][0]["uuid"] == "ab-12"
    assert body["model"]["id"] == "unbound"


@respx.mock
async def test_read_model_unreachable_degrades(api_client, db_engine, monkeypatch):
    import httpx
    monkeypatch.setattr(catalog_provider, "get_catalog", _fake_get_catalog)
    respx.get(f"{_DEV_URL}/api/unbound/settings/get").mock(side_effect=httpx.ConnectError("down"))
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid, base_url=_DEV_URL)
    await _login(api_client)
    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/catalog/models/unbound",
        headers=csrf_headers(api_client))
    assert r.status_code == 200
    body = r.json()
    assert body["reachable"] is False and body["values"] == {}


async def test_read_model_unknown_404(api_client, db_engine, monkeypatch):
    monkeypatch.setattr(catalog_provider, "get_catalog", _fake_get_catalog)
    monkeypatch.setattr(catalog_provider, "get_plugins_catalog", _no_catalog)  # no network in the fallback
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _login(api_client)
    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/catalog/models/nope",
        headers=csrf_headers(api_client))
    assert r.status_code == 404


async def test_read_model_denylist_is_read_only_no_live(api_client, db_engine, monkeypatch):
    monkeypatch.setattr(catalog_provider, "get_catalog", _fake_get_catalog)
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _login(api_client)
    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/catalog/models/interfaces",
        headers=csrf_headers(api_client))
    assert r.status_code == 200
    body = r.json()
    assert body["read_only"] is True
    assert body["values"] == {} and body["reachable"] is False


async def test_create_catalog_change_no_reconfigure_endpoint_422(api_client, db_engine, monkeypatch):
    # A model whose catalog entry has no reconfigure endpoint cannot be applied safely (partial-apply
    # hazard: scalars/grids would write, then the reload fails) -> refuse at proposal time.
    no_recon = {
        "edition": "community", "version": "26.1.8",
        "models": {"weird": {"id": "weird", "model_root": "weird",
                             "endpoints": {"get": "weird/settings/get", "set": "weird/settings/set"},
                             "fields": [{"path": "general.x", "type": "string"}], "grids": []}},
    }

    async def _fake(session, edition, version, **kw):
        return no_recon

    monkeypatch.setattr(catalog_provider, "get_catalog", _fake)
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _login(api_client)
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/catalog/changes",
        json={"model_id": "weird", "scalars": {"general.x": "1"}},
        headers=csrf_headers(api_client))
    assert r.status_code == 422


@respx.mock
async def test_read_model_returns_live_options(api_client, db_engine, monkeypatch):
    monkeypatch.setattr(catalog_provider, "get_catalog", _fake_get_catalog)
    payload = {"unbound": {
        "general": {"enabled": "1",
                    "outgoing": {"lan": {"value": "LAN", "selected": "1"}}},
        "hosts": {"ab": {"hostname": "web", "server": "10.0.0.10"}},
    }}
    respx.get("https://203.0.113.10/api/unbound/settings/get").mock(
        return_value=Response(200, json=payload))
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid, base_url="https://203.0.113.10")
    await _login(api_client)
    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/catalog/models/unbound", headers=csrf_headers(api_client))
    assert r.status_code == 200
    body = r.json()
    assert body["field_options"]["general.outgoing"] == [{"value": "lan", "label": "LAN"}]
    assert "grid_field_options" in body  # present (may be empty for this model's plain-string grid)
