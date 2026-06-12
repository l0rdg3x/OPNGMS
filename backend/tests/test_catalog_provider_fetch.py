import hashlib
import json

import httpx
import respx
from httpx import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.catalog_cache import CatalogCache
from app.services.catalog_provider import get_catalog, get_model

_BASE = "https://catalogs.test"
_CATALOG = {"edition": "community", "version": "26.1.8", "models": {"unbound": {"id": "unbound"}}}
_BLOB = (json.dumps(_CATALOG)).encode("utf-8")
_SHA = hashlib.sha256(_BLOB).hexdigest()


def _mock_release(catalog_blob=_BLOB, sha=_SHA, business=None):
    respx.get(f"{_BASE}/manifest.json").mock(
        return_value=Response(200, json={"generated_at": "", "catalogs": {"community/26.1.8": sha}}))
    respx.get(f"{_BASE}/community-26.1.8.json").mock(return_value=Response(200, content=catalog_blob))
    if business is not None:
        respx.get(f"{_BASE}/business-base.json").mock(return_value=Response(200, json=business))


@respx.mock
async def test_get_catalog_fetches_verifies_and_caches(db_engine):
    _mock_release()
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        cat = await get_catalog(s, "community", "26.1.8", base_url=_BASE, auto_fetch=True)
        assert cat["version"] == "26.1.8"
        await s.commit()
    async with factory() as s:
        rows = (await s.execute(select(CatalogCache))).scalars().all()
        assert len(rows) == 1 and rows[0].sha256 == _SHA


@respx.mock
async def test_get_catalog_rejects_sha_mismatch(db_engine):
    # manifest advertises a sha that does NOT match the served bytes -> reject, do not cache.
    _mock_release(sha="deadbeef")
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        cat = await get_catalog(s, "community", "26.1.8", base_url=_BASE, auto_fetch=True)
        assert cat is None
        assert (await s.execute(select(CatalogCache))).first() is None


@respx.mock
async def test_get_catalog_warm_cache_skips_download(db_engine):
    _mock_release()
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await get_catalog(s, "community", "26.1.8", base_url=_BASE, auto_fetch=True)
        await s.commit()
    # Second call: manifest still served, but the catalog route is removed -> must hit cache.
    respx.get(f"{_BASE}/community-26.1.8.json").mock(side_effect=AssertionError("should not download"))
    async with factory() as s:
        cat = await get_catalog(s, "community", "26.1.8", base_url=_BASE, auto_fetch=True)
        assert cat["version"] == "26.1.8"


@respx.mock
async def test_get_catalog_offline_serves_cached(db_engine):
    # Pre-seed a cache row, then make the manifest unreachable.
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        s.add(CatalogCache(edition="community", version="26.1.8", sha256=_SHA, content=_CATALOG))
        await s.commit()
    respx.get(f"{_BASE}/manifest.json").mock(side_effect=httpx.ConnectError("offline"))
    async with factory() as s:
        cat = await get_catalog(s, "community", "26.1.8", base_url=_BASE, auto_fetch=True)
        assert cat["version"] == "26.1.8"


@respx.mock
async def test_get_catalog_offline_cold_returns_none(db_engine):
    respx.get(f"{_BASE}/manifest.json").mock(side_effect=httpx.ConnectError("offline"))
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        assert await get_catalog(s, "community", "26.1.8", base_url=_BASE, auto_fetch=True) is None


@respx.mock
async def test_get_catalog_business_resolves_to_community_base(db_engine):
    # BE 26.4 -> CE 26.1.6; serve the Community catalog, cache under ("community","26.1.6").
    biz_catalog = {"edition": "community", "version": "26.1.6", "models": {}}
    blob = json.dumps(biz_catalog).encode()
    sha = hashlib.sha256(blob).hexdigest()
    respx.get(f"{_BASE}/manifest.json").mock(
        return_value=Response(200, json={"generated_at": "", "catalogs": {"community/26.1.6": sha}}))
    respx.get(f"{_BASE}/business-base.json").mock(
        return_value=Response(200, json={"map": {"26.4": "26.1.6"}}))
    respx.get(f"{_BASE}/community-26.1.6.json").mock(return_value=Response(200, content=blob))
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        cat = await get_catalog(s, "business", "26.4", base_url=_BASE, auto_fetch=True)
        assert cat["version"] == "26.1.6"
        await s.commit()
    async with factory() as s:
        row = (await s.execute(select(CatalogCache))).scalar_one()
        assert (row.edition, row.version) == ("community", "26.1.6")


@respx.mock
async def test_get_model_returns_named_model(db_engine):
    _mock_release()
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        model = await get_model(s, "community", "26.1.8", "unbound", base_url=_BASE, auto_fetch=True)
        assert model == {"id": "unbound"}
        assert await get_model(s, "community", "26.1.8", "nope", base_url=_BASE, auto_fetch=True) is None
