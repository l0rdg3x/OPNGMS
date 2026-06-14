"""API tests for GET /api/tenants/{tid}/perimeter/attackers: ranking, label, country, RBAC, kind."""
import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.api.monitoring as monitoring
from tests.factories import make_membership, make_tenant, make_user


class _FakeGeoip:
    def __init__(self, mapping):
        self._m = mapping

    def country(self, ip):
        return self._m.get(ip)


def _patch_get_geoip(monkeypatch, result):
    async def _stub(session):
        return result

    monkeypatch.setattr(monitoring, "get_geoip", _stub, raising=True)


async def _seed_members(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        admin = await make_user(s, email="ta@x.io", password="pw12345-secure")
        await make_membership(s, user_id=admin.id, tenant_id=t.id, role="tenant_admin")
        viewer = await make_user(s, email="ro@x.io", password="pw12345-secure")
        await make_membership(s, user_id=viewer.id, tenant_id=t.id, role="read_only")
        await s.commit()
        return t.id


async def _seed_rollup(db_engine, tid):
    """A device + firewall_block (two IPs, different counts) + a login_failed row, all recent."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
    async with factory() as s:
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        rows = [
            ("firewall_block", "203.0.113.9", 7, {"top_ports": ["23", "80"]}),
            ("firewall_block", "203.0.113.10", 2, {"top_ports": ["443"]}),
            ("login_failed", "198.51.100.4", 5, {"last_username": "admin", "usernames": ["admin", "root"]}),
        ]
        for kind, ip, c, detail in rows:
            await s.execute(text(
                "INSERT INTO perimeter_attacker (device_id,kind,src_ip,tenant_id,count,first_seen,last_seen,detail) "
                "VALUES (:d,:k,:ip,:t,:c,:n,:n,(:detail)::jsonb)"),
                {"d": did, "k": kind, "ip": ip, "t": tid, "c": c, "n": now,
                 "detail": json.dumps(detail)})
        await s.commit()
    return now


def _params(now, **extra):
    return {"frm": (now - timedelta(days=1)).isoformat(), "to": (now + timedelta(days=1)).isoformat(), **extra}


async def _login(client, email):
    await client.post("/api/login", json={"email": email, "password": "pw12345-secure"})


async def test_firewall_blocks_ranked_with_country_and_port(api_client, db_engine, monkeypatch):
    tid = await _seed_members(db_engine)
    now = await _seed_rollup(db_engine, tid)
    _patch_get_geoip(monkeypatch, _FakeGeoip({"203.0.113.9": "RU", "203.0.113.10": "US"}))
    await _login(api_client, "ta@x.io")

    r = await api_client.get(f"/api/tenants/{tid}/perimeter/attackers", params=_params(now, kind="firewall_block"))
    assert r.status_code == 200
    body = r.json()
    assert [row["src_ip"] for row in body] == ["203.0.113.9", "203.0.113.10"]  # ranked by count desc
    assert body[0]["country"] == "RU" and body[0]["count"] == 7 and body[0]["label"] == "23"
    assert body[1]["country"] == "US"


async def test_login_failed_label_is_username(api_client, db_engine, monkeypatch):
    tid = await _seed_members(db_engine)
    now = await _seed_rollup(db_engine, tid)
    _patch_get_geoip(monkeypatch, _FakeGeoip({}))
    await _login(api_client, "ta@x.io")

    r = await api_client.get(f"/api/tenants/{tid}/perimeter/attackers", params=_params(now, kind="login_failed"))
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1 and body[0]["src_ip"] == "198.51.100.4"
    assert body[0]["label"] == "admin" and body[0]["country"] == "UNKNOWN"  # no geoip hit -> UNKNOWN


async def test_unknown_kind_is_422(api_client, db_engine, monkeypatch):
    tid = await _seed_members(db_engine)
    now = await _seed_rollup(db_engine, tid)
    _patch_get_geoip(monkeypatch, _FakeGeoip({}))
    await _login(api_client, "ta@x.io")
    r = await api_client.get(f"/api/tenants/{tid}/perimeter/attackers", params=_params(now, kind="nope"))
    assert r.status_code == 422


async def test_read_only_may_view(api_client, db_engine, monkeypatch):
    tid = await _seed_members(db_engine)
    now = await _seed_rollup(db_engine, tid)
    _patch_get_geoip(monkeypatch, _FakeGeoip({}))
    await _login(api_client, "ro@x.io")
    r = await api_client.get(f"/api/tenants/{tid}/perimeter/attackers", params=_params(now, kind="firewall_block"))
    assert r.status_code == 200 and len(r.json()) == 2


async def test_no_geoip_still_lists_attackers(api_client, db_engine, monkeypatch):
    # Unlike attacker-countries (country-based -> []), the perimeter view is IP-based: no mmdb -> UNKNOWN.
    tid = await _seed_members(db_engine)
    now = await _seed_rollup(db_engine, tid)
    _patch_get_geoip(monkeypatch, None)
    await _login(api_client, "ta@x.io")
    r = await api_client.get(f"/api/tenants/{tid}/perimeter/attackers", params=_params(now, kind="firewall_block"))
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2 and all(row["country"] == "UNKNOWN" for row in body)
