import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.factories import make_membership, make_tenant, make_user

# A representative IDS `get` response (real-26.1.9 shape): option-objects, "0"/"1", strings, and the
# per-device `interfaces` hardware field that the catalog excludes from the inferred schema.
_IDS_GET = {
    "ids": {
        "general": {
            "enabled": "0",
            "mode": {"pcap": {"value": "PCAP", "selected": 1}},
            "interfaces": {"wan": {"value": "WAN", "selected": 1}},
            "homenet": {"192.168.0.0/16": {"value": "192.168.0.0/16", "selected": 1}},
            "AlertSaveLogs": "4",
        }
    }
}


async def _seed_members(db_engine):
    """Create a tenant with a tenant_admin and a read_only member."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        admin = await make_user(s, email="ta@x.io", password="pw12345-secure")
        await make_membership(s, user_id=admin.id, tenant_id=t.id, role="tenant_admin")
        viewer = await make_user(s, email="ro@x.io", password="pw12345-secure")
        await make_membership(s, user_id=viewer.id, tenant_id=t.id, role="read_only")
        await s.commit()
        return t.id


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


async def _login(api_client, email):
    await api_client.post("/api/login", json={"email": email, "password": "pw12345-secure"})


async def test_list_setting_endpoints_requires_auth_and_lists_ids_general(api_client, db_engine):
    # any authenticated user may read the catalog (it powers the kind picker)
    await _seed_members(db_engine)
    await _login(api_client, "ro@x.io")
    r = await api_client.get("/api/opnsense/setting-endpoints")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert any(e["key"] == "ids_general" for e in body)
    entry = next(e for e in body if e["key"] == "ids_general")
    assert "label" in entry


async def test_introspect_setting_returns_schema_and_omits_excluded_hardware_field(
    api_client, db_engine, monkeypatch
):
    # introspecting a device's IDS-general setting returns an inferred field schema; the per-device
    # hardware field (general.interfaces) is excluded by the catalog and absent from the schema.
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)

    async def _stub_get_setting(self, get_path):
        return _IDS_GET

    monkeypatch.setattr(
        "app.connectors.opnsense.client.OpnsenseClient.get_setting", _stub_get_setting
    )
    # the device row carries empty (placeholder) encrypted creds; the endpoint builds an
    # OpnsenseClient (decrypting them) before calling get_setting — stub decrypt so it builds.
    monkeypatch.setattr("app.core.crypto.decrypt", lambda blob: "x")

    await _login(api_client, "ta@x.io")
    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/opnsense/settings/ids_general"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["endpoint_key"] == "ids_general"
    paths = {f["path"] for f in body["fields"]}
    # the excluded per-device hardware field must NOT appear
    assert "general.interfaces" not in paths
    # but the portable fields are present
    assert "general.enabled" in paths
    assert "general.homenet" in paths


async def test_introspect_unknown_endpoint_is_404(api_client, db_engine):
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)
    await _login(api_client, "ta@x.io")
    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/opnsense/settings/does_not_exist"
    )
    assert r.status_code == 404


async def test_introspect_cross_tenant_device_is_404(api_client, db_engine):
    # a device that belongs to another tenant must not be readable through this tenant's path
    tid = await _seed_members(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        other = await make_tenant(s, slug="other")
        await s.commit()
        other_tid = other.id
    did = await _insert_device(db_engine, other_tid, name="otherfw")
    await _login(api_client, "ta@x.io")
    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/opnsense/settings/ids_general"
    )
    assert r.status_code == 404
