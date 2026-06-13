import hashlib
import json

import httpx
import respx
from httpx import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.catalog_cache import CatalogCache
from app.services.catalog_provider import get_plugins_catalog

_BASE = "https://catalogs.test"
_PCAT = {"edition": "community", "version": "26.1.9", "generated_from": {"plugins": "26.1.9"},
         "models": {"haproxy": {"id": "haproxy", "source": "plugins",
                                "plugin": {"package": "os-haproxy"}}}, "menu": []}
_PBLOB = json.dumps(_PCAT).encode("utf-8")
_PSHA = hashlib.sha256(_PBLOB).hexdigest()


def _mock_plugins(sha=_PSHA, blob=_PBLOB):
    respx.get(f"{_BASE}/manifest.json").mock(return_value=Response(
        200, json={"generated_at": "", "catalogs": {"community/26.1.9": "x",
                                                     "community-plugins/26.1.9": sha}}))
    respx.get(f"{_BASE}/community-plugins-26.1.9.json").mock(return_value=Response(200, content=blob))


@respx.mock
async def test_get_plugins_catalog_fetches_verifies_and_caches(db_engine):
    _mock_plugins()
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        cat = await get_plugins_catalog(s, "community", "26.1.9", base_url=_BASE, auto_fetch=True)
        assert cat["models"]["haproxy"]["plugin"]["package"] == "os-haproxy"
        await s.commit()
    async with factory() as s:
        row = (await s.execute(
            select(CatalogCache).where(CatalogCache.edition == "community-plugins"))).scalar_one()
        assert (row.edition, row.version, row.sha256) == ("community-plugins", "26.1.9", _PSHA)


@respx.mock
async def test_get_plugins_catalog_rejects_sha_mismatch(db_engine):
    _mock_plugins(sha="deadbeef")
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        assert await get_plugins_catalog(s, "community", "26.1.9", base_url=_BASE, auto_fetch=True) is None
        assert (await s.execute(select(CatalogCache))).first() is None


@respx.mock
async def test_get_plugins_catalog_floor_resolves_version(db_engine):
    # device on 26.1.10 but only 26.1.9 plugins published -> serve 26.1.9.
    _mock_plugins()
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        cat = await get_plugins_catalog(s, "community", "26.1.10", base_url=_BASE, auto_fetch=True)
        assert cat["version"] == "26.1.9"


@respx.mock
async def test_get_plugins_catalog_business_uses_community_plugins(db_engine):
    # Business device: resolve its base via business-base, then serve the community-plugins asset.
    respx.get(f"{_BASE}/manifest.json").mock(return_value=Response(
        200, json={"generated_at": "", "catalogs": {"community-plugins/26.1.6": _PSHA}}))
    respx.get(f"{_BASE}/business-base.json").mock(return_value=Response(200, json={"map": {"26.4": "26.1.6"}}))
    # served bytes carry version 26.1.6
    cat66 = dict(_PCAT, version="26.1.6")
    blob = json.dumps(cat66).encode()
    sha = hashlib.sha256(blob).hexdigest()
    respx.get(f"{_BASE}/manifest.json").mock(return_value=Response(
        200, json={"generated_at": "", "catalogs": {"community-plugins/26.1.6": sha}}))
    respx.get(f"{_BASE}/community-plugins-26.1.6.json").mock(return_value=Response(200, content=blob))
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        cat = await get_plugins_catalog(s, "business", "26.4", base_url=_BASE, auto_fetch=True)
        assert cat["version"] == "26.1.6"


@respx.mock
async def test_get_plugins_catalog_offline_cold_returns_none(db_engine):
    respx.get(f"{_BASE}/manifest.json").mock(side_effect=httpx.ConnectError("offline"))
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        assert await get_plugins_catalog(s, "community", "26.1.9", base_url=_BASE, auto_fetch=True) is None
