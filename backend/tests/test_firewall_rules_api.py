import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.factories import make_membership, make_tenant, make_user

_MODEL = {"action": {"pass": {"value": "Pass", "selected": 1}},
          "interface": {"wan": {"value": "WAN", "selected": 0}},
          "gateway": {"": {"value": "none", "selected": 1}},
          "source_net": "any", "log": "0", "description": ""}


async def _seed_members(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        admin = await make_user(s, email="ta@x.io", password="pw12345")
        await make_membership(s, user_id=admin.id, tenant_id=t.id, role="tenant_admin")
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
    await api_client.post("/api/login", json={"email": email, "password": "pw12345"})


async def test_rule_model_returns_fields_and_interfaces(api_client, db_engine, monkeypatch):
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)

    async def _stub(self):
        return _MODEL

    monkeypatch.setattr(
        "app.connectors.opnsense.client.OpnsenseClient.get_firewall_rule_model", _stub)
    monkeypatch.setattr("app.core.crypto.decrypt", lambda blob: "x")

    await _login(api_client, "ta@x.io")
    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/opnsense/firewall/rule-model")
    assert r.status_code == 200
    body = r.json()
    paths = {f["path"] for f in body["fields"]}
    assert "action" in paths and "interface" not in paths and "gateway" not in paths
    assert {i["value"] for i in body["interfaces"]} == {"wan"}


async def test_rule_model_cross_tenant_is_404(api_client, db_engine):
    tid = await _seed_members(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        other = await make_tenant(s, slug="other")
        await s.commit()
        other_tid = other.id
    did = await _insert_device(db_engine, other_tid, name="otherfw")
    await _login(api_client, "ta@x.io")
    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/opnsense/firewall/rule-model")
    assert r.status_code == 404
