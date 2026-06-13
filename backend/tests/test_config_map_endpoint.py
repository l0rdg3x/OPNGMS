"""Endpoint tests for GET /config/map (Task 6, sub-project 3c).

Mirrors tests/test_config_model_api.py for the device + encrypted snapshot fixtures, and monkeypatches
the connector's get_config_backup for the live/unreachable paths and catalog_provider.get_catalog for
the catalog cross-reference.
"""
import gzip
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.connectors.opnsense.client import OpnsenseClient, OpnsenseError
from app.core import crypto
from app.services import catalog_provider
from tests.conftest import csrf_headers
from tests.factories import make_membership, make_tenant, make_user

# A config with a sensitive <password> node and an unboundplus section that the catalog covers.
XML = (
    "<opnsense>"
    "<revision><time>1000</time></revision>"
    "<system><hostname>fw1</hostname>"
    "<user><name>root</name><password>topsecret</password></user></system>"
    "<unboundplus><general><enabled>1</enabled></general></unboundplus>"
    "<legacything><foo>bar</foo></legacything>"
    "</opnsense>"
)

# One catalog model mounted at the unboundplus section -> that subtree is editable.
CATALOG = {
    "edition": "community", "version": "26.1.9",
    "models": {"unbound.x": {"id": "unbound.x", "xml_path": "OPNsense/unboundplus",
                             "fields": [{"path": "general.enabled", "type": "bool"}]}},
}


async def _fake_get_catalog(session, edition, version, **kw):
    return CATALOG


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


async def _seed_snapshot(db_engine, tenant_id, device_id, xml, canonical_hash, taken_at_sql="now()"):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    sid = uuid.uuid4()
    content_enc = crypto.encrypt_bytes(gzip.compress(xml.encode("utf-8")))
    async with factory() as s:
        await s.execute(text(
            "INSERT INTO config_snapshots "
            "(id, tenant_id, device_id, taken_at, canonical_hash, content_enc, opnsense_version, size_bytes) "
            f"VALUES (:id, :t, :d, {taken_at_sql}, :h, :c, '24.7', :sz)"),
            {"id": sid, "t": tenant_id, "d": device_id, "h": canonical_hash,
             "c": content_enc, "sz": len(xml.encode("utf-8"))})
        await s.commit()
    return sid


async def _login(api_client, email="ta@x.io"):
    await api_client.post("/api/login", json={"email": email, "password": "pw12345-secure"})


def _find(node, tag):
    """Depth-first: first node whose tag == `tag`, or None."""
    if node.get("tag") == tag:
        return node
    for c in node.get("children", []):
        hit = _find(c, tag)
        if hit is not None:
            return hit
    return None


async def test_config_map_live_annotated(api_client, db_engine, monkeypatch):
    monkeypatch.setattr(catalog_provider, "get_catalog", _fake_get_catalog)
    monkeypatch.setattr(OpnsenseClient, "get_config_backup", lambda self: _as_coro(XML))
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _login(api_client)

    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/config/map", headers=csrf_headers(api_client))
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "live" and body["reachable"] is True
    tree = body["tree"]
    assert tree["tag"] == "opnsense"
    # The unboundplus subtree is covered by the catalog model -> editable.
    unbound = _find(tree, "unboundplus")
    assert unbound["editable"] is True
    assert unbound["catalog_model_id"] == "unbound.x"
    # A subtree node inherits coverage.
    general = _find(unbound, "general")
    assert general["editable"] is True
    # The legacy section has no catalog model -> read-only.
    legacy = _find(tree, "legacything")
    assert legacy["editable"] is False
    assert "catalog_model_id" not in legacy
    # SECURITY: the seeded secret is redacted by build_tree, never in the response.
    assert "topsecret" not in r.text


async def test_config_map_redacts_password(api_client, db_engine, monkeypatch):
    monkeypatch.setattr(catalog_provider, "get_catalog", _fake_get_catalog)
    monkeypatch.setattr(OpnsenseClient, "get_config_backup", lambda self: _as_coro(XML))
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _login(api_client)

    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/config/map", headers=csrf_headers(api_client))
    assert r.status_code == 200
    pw = _find(r.json()["tree"], "password")
    assert pw["sensitive"] is True
    assert pw["value"] is None
    assert "topsecret" not in r.text


async def test_config_map_unreachable_falls_back_to_snapshot(api_client, db_engine, monkeypatch):
    monkeypatch.setattr(catalog_provider, "get_catalog", _fake_get_catalog)

    async def _boom(self):
        raise OpnsenseError("device down")

    monkeypatch.setattr(OpnsenseClient, "get_config_backup", _boom)
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _seed_snapshot(db_engine, tid, did, XML, "hashA")
    await _login(api_client)

    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/config/map", headers=csrf_headers(api_client))
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "snapshot" and body["reachable"] is False
    assert body["taken_at"] is not None
    # The snapshot tree is still annotated against the catalog.
    unbound = _find(body["tree"], "unboundplus")
    assert unbound["editable"] is True and unbound["catalog_model_id"] == "unbound.x"
    assert "topsecret" not in r.text


async def test_config_map_no_snapshot_and_unreachable_404(api_client, db_engine, monkeypatch):
    monkeypatch.setattr(catalog_provider, "get_catalog", _fake_get_catalog)

    async def _boom(self):
        raise OpnsenseError("device down")

    monkeypatch.setattr(OpnsenseClient, "get_config_backup", _boom)
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _login(api_client)

    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/config/map", headers=csrf_headers(api_client))
    assert r.status_code == 404


async def test_config_map_no_catalog_still_renders_read_only(api_client, db_engine, monkeypatch):
    """A device version with no catalog still returns the tree (all read-only), not a 404."""
    async def _none(session, edition, version, **kw):
        return None

    monkeypatch.setattr(catalog_provider, "get_catalog", _none)
    monkeypatch.setattr(OpnsenseClient, "get_config_backup", lambda self: _as_coro(XML))
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _login(api_client)

    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/config/map", headers=csrf_headers(api_client))
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "live"
    assert _find(body["tree"], "unboundplus")["editable"] is False


async def test_config_map_cross_tenant_404(api_client, db_engine, monkeypatch):
    monkeypatch.setattr(catalog_provider, "get_catalog", _fake_get_catalog)
    monkeypatch.setattr(OpnsenseClient, "get_config_backup", lambda self: _as_coro(XML))
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    other = uuid.uuid4()
    await _login(api_client)

    r = await api_client.get(
        f"/api/tenants/{other}/devices/{did}/config/map", headers=csrf_headers(api_client))
    assert r.status_code == 404


async def _as_coro(value):
    """Wrap a plain value so a lambda can stand in for an async method."""
    return value
