import gzip
import uuid

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import crypto
from app.main import app
from tests.factories import make_tenant, make_user

# Two snapshots of the same device: a hostname + a secret password change.
# The secret values MUST NOT leak through any endpoint (metadata-only / paths-only).
XML_A = (
    "<opnsense>"
    "<revision><time>1000</time></revision>"
    "<system><hostname>fw1</hostname><user><password>topsecret1</password></user></system>"
    "</opnsense>"
)
XML_B = (
    "<opnsense>"
    "<revision><time>2000</time></revision>"
    "<system><hostname>fw2</hostname><user><password>topsecret2</password></user></system>"
    "</opnsense>"
)


async def _login_superadmin(api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tid = t.id
    await api_client.post(
        "/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345-secure"}
    )
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345-secure"})
    return tid


async def _insert_device(db_engine, tenant_id, name="fw1"):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices "
                "(id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id, :t, :n, 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tenant_id, "n": name},
        )
        await s.commit()
    return did


async def _seed_snapshot(db_engine, tenant_id, device_id, xml, canonical_hash, taken_at_sql="now()"):
    """Insert one encrypted snapshot (Fernet(gzip(xml))) and return its id."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    sid = uuid.uuid4()
    content_enc = crypto.encrypt_bytes(gzip.compress(xml.encode("utf-8")))
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO config_snapshots "
                "(id, tenant_id, device_id, taken_at, canonical_hash, content_enc, opnsense_version, size_bytes) "
                f"VALUES (:id, :t, :d, {taken_at_sql}, :h, :c, '24.7', :sz)"
            ),
            {
                "id": sid,
                "t": tenant_id,
                "d": device_id,
                "h": canonical_hash,
                "c": content_enc,
                "sz": len(xml.encode("utf-8")),
            },
        )
        await s.commit()
    return sid


async def test_snapshots_endpoint_returns_metadata_without_content(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)
    sid = await _seed_snapshot(db_engine, tid, did, XML_A, "hashA")

    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/config/snapshots")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    snap = body[0]
    # Metadata is exposed.
    assert snap["id"] == str(sid)
    assert snap["device_id"] == str(did)
    assert snap["canonical_hash"] == "hashA"
    assert snap["opnsense_version"] == "24.7"
    assert "taken_at" in snap and "size_bytes" in snap
    # SECURITY: the encrypted content / secrets must NEVER be exposed.
    assert "content" not in snap
    assert "content_enc" not in snap
    blob = r.text
    assert "topsecret1" not in blob
    assert "topsecret2" not in blob


async def test_snapshots_endpoint_orders_newest_first(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)
    await _seed_snapshot(db_engine, tid, did, XML_A, "hashA", taken_at_sql="now() - interval '1 hour'")
    await _seed_snapshot(db_engine, tid, did, XML_B, "hashB", taken_at_sql="now()")

    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/config/snapshots")
    assert r.status_code == 200
    assert [s["canonical_hash"] for s in r.json()] == ["hashB", "hashA"]


async def test_drift_endpoint_returns_counts(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)
    await _seed_snapshot(db_engine, tid, did, XML_A, "hashA", taken_at_sql="now() - interval '1 hour'")
    await _seed_snapshot(db_engine, tid, did, XML_B, "hashB", taken_at_sql="now()")

    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/config/drift")
    assert r.status_code == 200
    body = r.json()
    assert body["version_count"] == 2
    assert body["changed_since_previous"] is True
    assert body["latest_taken_at"] is not None


async def test_drift_endpoint_empty_device(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)

    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/config/drift")
    assert r.status_code == 200
    body = r.json()
    assert body["version_count"] == 0
    assert body["changed_since_previous"] is False
    assert body["latest_taken_at"] is None


async def test_diff_endpoint_returns_paths_without_values(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)
    sa = await _seed_snapshot(db_engine, tid, did, XML_A, "hashA")
    sb = await _seed_snapshot(db_engine, tid, did, XML_B, "hashB")

    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/config/diff",
        params={"from": str(sa), "to": str(sb)},
    )
    assert r.status_code == 200
    changes = {c["path"]: c["change"] for c in r.json()}
    assert changes["opnsense/system/hostname"] == "modified"
    assert changes["opnsense/system/user/password"] == "modified"
    # SECURITY: the structural diff exposes paths only — never element values.
    blob = r.text
    assert "topsecret1" not in blob
    assert "topsecret2" not in blob
    assert "fw1" not in blob
    assert "fw2" not in blob


async def test_diff_endpoint_404_for_unknown_snapshot(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)
    sa = await _seed_snapshot(db_engine, tid, did, XML_A, "hashA")

    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/config/diff",
        params={"from": str(sa), "to": str(uuid.uuid4())},
    )
    assert r.status_code == 404


async def test_diff_endpoint_404_for_snapshot_of_other_device(api_client, db_engine):
    """A snapshot id that exists but belongs to a different device must 404 (not leak)."""
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid, name="fw1")
    other = await _insert_device(db_engine, tid, name="fw2")
    sa = await _seed_snapshot(db_engine, tid, did, XML_A, "hashA")
    sb_other = await _seed_snapshot(db_engine, tid, other, XML_B, "hashB")

    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/config/diff",
        params={"from": str(sa), "to": str(sb_other)},
    )
    assert r.status_code == 404


async def test_config_requires_auth(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as anon:
        r = await anon.get(f"/api/tenants/{tid}/devices/{did}/config/snapshots")
    assert r.status_code == 401


async def test_config_forbidden_without_membership(api_client, db_engine):
    """A non-superadmin user without a membership on the tenant gets a 403."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await make_user(s, email="sa@x.io", password="pw12345-secure", is_superadmin=True)
        await make_user(s, email="other@x.io", password="pw12345-secure", is_superadmin=False)
        await s.commit()
        tid = t.id
    did = await _insert_device(db_engine, tid)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as c:
        login = await c.post(
            "/api/login", json={"email": "other@x.io", "password": "pw12345-secure"}
        )
        assert login.status_code == 200
        r = await c.get(f"/api/tenants/{tid}/devices/{did}/config/snapshots")
    assert r.status_code == 403
