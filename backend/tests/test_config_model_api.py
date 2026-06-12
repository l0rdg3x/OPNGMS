import gzip
import uuid

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import crypto
from app.main import app
from tests.factories import make_tenant, make_user

# A config with a sensitive password node and two interfaces.
# The secret value MUST NOT leak through any model/capabilities response.
XML = (
    "<opnsense>"
    "<revision><time>1000</time></revision>"
    "<system><hostname>fw1</hostname>"
    "<user><name>root</name><password>topsecret</password></user></system>"
    "<interfaces>"
    "<wan><if>igb0</if><descr>WAN</descr></wan>"
    "<lan><if>igb1</if><descr>LAN</descr></lan>"
    "</interfaces>"
    "<filter><rule><type>pass</type></rule></filter>"
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
    """Insert a device with REAL encrypted credentials (decryptable server-side).

    base_url is unresolvable, so a live probe fails with a connector error: the
    capabilities endpoint must degrade to empirical-only (resilience path).
    """
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    key_enc = crypto.encrypt("apikey")
    secret_enc = crypto.encrypt("apisecret")
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices "
                "(id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id, :t, :n, 'https://x', :k, :sec, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tenant_id, "n": name, "k": key_enc, "sec": secret_enc},
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


async def test_config_model_returns_tree_with_redacted_secret(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)
    await _seed_snapshot(db_engine, tid, did, XML, "hashA")

    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/config/model")
    assert r.status_code == 200
    body = r.json()
    assert body["tag"] == "opnsense"
    # <revision> stripped; order preserved.
    top = [c["tag"] for c in body["children"]]
    assert top == ["system", "interfaces", "filter"]

    # SECURITY: the seeded secret must NEVER appear anywhere in the model JSON.
    assert "topsecret" not in r.text

    # Locate the password node: opnsense/system/user/password.
    system = body["children"][0]
    user = [c for c in system["children"] if c["tag"] == "user"][0]
    pw = [c for c in user["children"] if c["tag"] == "password"][0]
    assert pw["sensitive"] is True
    assert pw["value"] is None


async def test_config_model_uses_latest_snapshot(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)
    old_xml = "<opnsense><system><hostname>old</hostname></system></opnsense>"
    new_xml = "<opnsense><system><hostname>new</hostname></system></opnsense>"
    await _seed_snapshot(db_engine, tid, did, old_xml, "old", taken_at_sql="now() - interval '1 hour'")
    await _seed_snapshot(db_engine, tid, did, new_xml, "new", taken_at_sql="now()")

    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/config/model")
    assert r.status_code == 200
    hostname = r.json()["children"][0]["children"][0]
    assert hostname["tag"] == "hostname"
    assert hostname["value"] == "new"


async def test_config_model_404_without_snapshot(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)

    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/config/model")
    assert r.status_code == 404


async def test_capabilities_resilient_to_probe_failure(api_client, db_engine):
    """The device is unreachable -> the live probe raises -> the endpoint must still
    return empirical data (interfaces/sections/version) with empty available_capabilities."""
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)
    await _seed_snapshot(db_engine, tid, did, XML, "hashA")

    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/config/capabilities")
    assert r.status_code == 200
    body = r.json()

    assert body["opnsense_version"] == "24.7"
    names = {i["name"] for i in body["interfaces"]}
    assert names == {"wan", "lan"}
    wan = [i for i in body["interfaces"] if i["name"] == "wan"][0]
    assert wan["nic"] == "igb0"
    assert wan["description"] == "WAN"
    assert "system" in body["configured_sections"]
    assert "interfaces" in body["configured_sections"]
    assert "filter" in body["configured_sections"]
    # Probe failed -> empirical-only, no plugin capabilities.
    assert body["available_capabilities"] == []

    # SECURITY: no secret leaks into the capabilities response either.
    assert "topsecret" not in r.text


async def test_capabilities_404_without_snapshot(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)

    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/config/capabilities")
    assert r.status_code == 404


async def test_config_model_requires_auth(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as anon:
        r = await anon.get(f"/api/tenants/{tid}/devices/{did}/config/model")
    assert r.status_code == 401


async def test_config_model_forbidden_without_membership(api_client, db_engine):
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
        r = await c.get(f"/api/tenants/{tid}/devices/{did}/config/model")
    assert r.status_code == 403
