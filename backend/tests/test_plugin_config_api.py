import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import config as config_mod
from app.models.catalog_cache import CatalogCache
from tests.factories import make_tenant

_CORE = {"edition": "community", "version": "26.1.9", "models": {
    "ids": {"id": "ids", "source": "core", "model_root": "ids", "xml_path": "OPNsense/IDS",
            "endpoints": {"get": "ids/settings/get"}, "fields": [], "grids": [], "pages": []}}, "menu": []}
_PLUGINS = {"edition": "community", "version": "26.1.9", "models": {
    "haproxy": {"id": "haproxy", "source": "plugins", "model_root": "haproxy",
                "xml_path": "OPNsense/HAProxy/general", "endpoints": {"get": "haproxy/settings/get"},
                "fields": [], "grids": [], "pages": [],
                "plugin": {"package": "os-haproxy", "title": "HAProxy", "category": "net", "version": "5.1"}}},
    "menu": []}


@pytest.fixture
def catalog_offline(monkeypatch):
    """Serve only the cached catalogs (no network): `catalog_auto_fetch=False`.

    `get_settings` is @lru_cache'd, so setting the env var alone won't take effect — clear the
    cache so the provider re-reads the flag, and clear it again on teardown so a False value
    doesn't leak into other tests.
    """
    monkeypatch.setenv("CATALOG_AUTO_FETCH", "false")
    config_mod.get_settings.cache_clear()
    yield
    config_mod.get_settings.cache_clear()


async def _setup(api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        tid = t.id
        s.add(CatalogCache(edition="community", version="26.1.9", sha256="a", content=_CORE))
        s.add(CatalogCache(edition="community-plugins", version="26.1.9", sha256="b", content=_PLUGINS))
        did = uuid.uuid4()
        await s.execute(text(
            "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls,"
            " status, tags, edition, firmware_version) VALUES (:id,:t,'fw','https://127.0.0.1:1',"
            "''::bytea,''::bytea,true,'reachable','{}','community','26.1.9')"), {"id": did, "t": tid})
        await s.commit()
    await api_client.post("/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345-secure"})
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345-secure"})
    return tid, did


async def test_plugin_model_fetch_falls_back_to_plugins_catalog(api_client, db_engine, catalog_offline):
    # auto_fetch off so the provider serves only the cached catalogs.
    tid, did = await _setup(api_client, db_engine)
    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/catalog/models/haproxy")
    assert r.status_code == 200
    assert r.json()["model"]["plugin"]["package"] == "os-haproxy"


async def test_plugin_models_map_lists_configurable_plugins(api_client, db_engine, catalog_offline):
    tid, did = await _setup(api_client, db_engine)
    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/plugin-models")
    assert r.status_code == 200
    assert r.json() == [{"package": "os-haproxy", "model_id": "haproxy", "title": "HAProxy"}]
