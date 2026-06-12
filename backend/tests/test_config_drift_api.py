import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import crypto
from app.core.db import set_tenant_context
from app.models.config_change import ConfigChange
from app.models.config_template import ConfigTemplate
from tests.factories import make_membership, make_user

_IDS_XML = (
    "<opnsense><OPNsense><IDS version='1.0'><general>"
    "<enabled>1</enabled></general></IDS></OPNsense></opnsense>"
)


class _FakeClient:
    """Stand-in for OpnsenseClient: returns canned live state, ignores constructor args."""
    def __init__(self, *a, **k):
        pass

    async def get_config_backup(self) -> str:
        return _IDS_XML

    async def list_ids_rulesets(self) -> list[dict]:
        return []


async def _seed(db_engine, *, payload, kind="opnsense_setting", target="ids_general",
                status="applied", source_template=True, encrypted_creds=True):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        admin = await make_user(s, email="admin@x.io", password="pw12345")
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await make_membership(s, user_id=admin.id, tenant_id=tid, role="tenant_admin")
        tpl_id = None
        if source_template:
            tpl = ConfigTemplate(kind=kind, name="t", description="", body={}, created_by=admin.id)
            s.add(tpl)
            await s.flush()
            tpl_id = tpl.id
        key = crypto.encrypt("k") if encrypted_creds else b""
        sec = crypto.encrypt("s") if encrypted_creds else b""
        await set_tenant_context(s, tid)
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',:k,:sec,true,'reachable','{}')"),
            {"i": did, "t": tid, "k": key, "sec": sec})
        c = ConfigChange(tenant_id=tid, device_id=did, created_by=admin.id, kind=kind,
                         operation="set", target=target, payload=payload, baseline_hash="",
                         status=status, source_template_id=tpl_id)
        s.add(c)
        await s.commit()
        return tid, did, c.id


async def _login(api_client, email="admin@x.io"):
    r = await api_client.post("/api/login", json={"email": email, "password": "pw12345"})
    assert r.status_code == 200, r.text


async def test_drift_check_reports_drift(api_client, db_engine, monkeypatch):
    import app.api.config as config_api
    monkeypatch.setattr(config_api, "OpnsenseClient", _FakeClient)
    # Applied "enabled=0" but the live box has "enabled=1" -> drift on general.enabled.
    tid, did, cid = await _seed(db_engine, payload={"endpoint_key": "ids_general",
                                                    "payload": {"general.enabled": "0"}})
    await _login(api_client)
    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/config/drift-check")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reachable"] is True
    [res] = body["results"]
    assert res["change_id"] == str(cid)
    assert res["status"] == "drifted"
    assert res["drifted_fields"] == ["general.enabled"]


async def test_drift_check_in_sync(api_client, db_engine, monkeypatch):
    import app.api.config as config_api
    monkeypatch.setattr(config_api, "OpnsenseClient", _FakeClient)
    tid, did, _ = await _seed(db_engine, payload={"endpoint_key": "ids_general",
                                                  "payload": {"general.enabled": "1"}})
    await _login(api_client)
    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/config/drift-check")
    assert r.status_code == 200, r.text
    assert r.json()["results"][0]["status"] == "in_sync"


async def test_drift_check_unreachable_when_creds_undecryptable(api_client, db_engine):
    # Empty/garbage api creds -> crypto.decrypt raises -> reachable=False, never 500.
    tid, did, _ = await _seed(db_engine, payload={"endpoint_key": "ids_general",
                                                  "payload": {"general.enabled": "0"}},
                              encrypted_creds=False)
    await _login(api_client)
    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/config/drift-check")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reachable"] is False
    assert body["results"] == []


async def test_drift_check_cross_tenant_device_is_404(app_role_api_client, db_engine):
    # tenant A admin asks for a device that belongs to tenant B -> RLS hides it -> 404.
    # Uses the RLS-enforcing app-role client so session.get(Device) is tenant-filtered.
    tidA, _, _ = await _seed(db_engine, payload={"endpoint_key": "ids_general",
                                                 "payload": {"general.enabled": "0"}})
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tidB, didB = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'B','b','active')"), {"i": tidB})
        await set_tenant_context(s, tidB)
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fwB','https://y',''::bytea,''::bytea,true,'reachable','{}')"), {"i": didB, "t": tidB})
        await s.commit()
    await _login(app_role_api_client)  # tenant A admin
    r = await app_role_api_client.get(f"/api/tenants/{tidA}/devices/{didB}/config/drift-check")
    assert r.status_code == 404, r.text
