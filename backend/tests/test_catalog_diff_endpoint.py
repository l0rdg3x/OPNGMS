"""Endpoint tests for GET /catalog/diff (Task 3, sub-project 3c).

Mirrors the harness in tests/test_catalog_api.py: a tenant + tenant_admin, a device on 26.1.9, the
api_client/db_engine fixtures, and monkeypatch of catalog_provider.get_catalog / published_versions.
"""
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import crypto
from app.services import catalog_provider
from tests.conftest import csrf_headers
from tests.factories import make_membership, make_tenant, make_user

CAT_FROM = {"edition": "community", "version": "26.1.8",
            "models": {"m": {"fields": [{"path": "a", "type": "string"}]}}}
CAT_TO = {"edition": "community", "version": "26.1.9",
          "models": {"m": {"fields": [{"path": "a", "type": "boolean"}, {"path": "b", "type": "string"}]}}}


async def _seed(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        admin = await make_user(s, email="ta@x.io", password="pw12345-secure")
        await make_membership(s, user_id=admin.id, tenant_id=t.id, role="tenant_admin")
        await s.commit()
        return t.id


async def _device(db_engine, tid, edition="community", version="26.1.9", base_url="https://x"):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
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


async def test_diff_default_previous(api_client, db_engine, monkeypatch):
    async def fake_get_catalog(session, edition, version, **kw):
        return CAT_TO if version == "26.1.9" else CAT_FROM

    async def fake_versions(edition="community"):
        return ["26.1.8", "26.1.9"]

    monkeypatch.setattr(catalog_provider, "get_catalog", fake_get_catalog)
    monkeypatch.setattr(catalog_provider, "published_versions", fake_versions)
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _login(api_client)

    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/catalog/diff", headers=csrf_headers(api_client))
    assert r.status_code == 200
    body = r.json()
    assert body["from"] == "26.1.8" and body["to"] == "26.1.9"
    assert body["available_baselines"] == ["26.1.8"]
    assert body["diff"]["models"]["m"]["added_fields"] == ["b"]
    assert body["diff"]["models"]["m"]["changed_fields"] == ["a"]


async def test_diff_explicit_from(api_client, db_engine, monkeypatch):
    async def fake_get_catalog(session, edition, version, **kw):
        return CAT_TO if version == "26.1.9" else CAT_FROM

    async def fake_versions(edition="community"):
        return ["26.1.7", "26.1.8", "26.1.9"]

    monkeypatch.setattr(catalog_provider, "get_catalog", fake_get_catalog)
    monkeypatch.setattr(catalog_provider, "published_versions", fake_versions)
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _login(api_client)

    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/catalog/diff?from=26.1.8",
        headers=csrf_headers(api_client))
    assert r.status_code == 200
    body = r.json()
    assert body["from"] == "26.1.8" and body["to"] == "26.1.9"
    # baselines = everything strictly below the device version
    assert body["available_baselines"] == ["26.1.7", "26.1.8"]
    assert body["diff"]["models"]["m"]["added_fields"] == ["b"]


async def test_diff_no_baseline_is_empty(api_client, db_engine, monkeypatch):
    async def fake_get_catalog(session, edition, version, **kw):
        return CAT_TO

    async def fake_versions(edition="community"):
        return ["26.1.9"]  # device is the lowest -> no previous

    monkeypatch.setattr(catalog_provider, "get_catalog", fake_get_catalog)
    monkeypatch.setattr(catalog_provider, "published_versions", fake_versions)
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _login(api_client)

    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/catalog/diff", headers=csrf_headers(api_client))
    assert r.status_code == 200
    body = r.json()
    assert body["from"] is None
    assert body["available_baselines"] == []
    assert body["diff"] == {"added_models": [], "removed_models": [], "models": {}}


async def test_diff_no_catalog_404(api_client, db_engine, monkeypatch):
    async def _none(session, edition, version, **kw):
        return None

    monkeypatch.setattr(catalog_provider, "get_catalog", _none)
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _login(api_client)

    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/catalog/diff", headers=csrf_headers(api_client))
    assert r.status_code == 404


async def test_diff_cross_tenant_404(api_client, db_engine, monkeypatch):
    async def fake_get_catalog(session, edition, version, **kw):
        return CAT_TO

    monkeypatch.setattr(catalog_provider, "get_catalog", fake_get_catalog)
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    other = uuid.uuid4()
    await _login(api_client)

    # A device that does not belong to `other` tenant -> 404 (ownership guard).
    r = await api_client.get(
        f"/api/tenants/{other}/devices/{did}/catalog/diff", headers=csrf_headers(api_client))
    assert r.status_code == 404
