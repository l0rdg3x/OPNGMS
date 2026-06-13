"""API tests for GET /api/tenants/{tid}/attacker-countries: list, RBAC, and degrade-to-[]."""
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import maxminddb
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.api.monitoring as monitoring
from app.main import app
from app.services.geoip import GeoIp
from tests.factories import make_membership, make_tenant, make_user

FIXTURE = Path(__file__).parent / "fixtures" / "geoip-test.mmdb"


def _fixture_geoip() -> GeoIp:
    return GeoIp(maxminddb.open_database(str(FIXTURE)))


def _patch_get_geoip(monkeypatch, result):
    """Replace the endpoint's `get_geoip` with an async stub returning `result` (a GeoIp or None)."""

    async def _stub(session):
        return result() if callable(result) else result

    monkeypatch.setattr(monitoring, "get_geoip", _stub, raising=True)


async def _seed_members(db_engine):
    """Tenant with a tenant_admin and a read_only member; returns the tenant id."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        admin = await make_user(s, email="ta@x.io", password="pw12345-secure")
        await make_membership(s, user_id=admin.id, tenant_id=t.id, role="tenant_admin")
        viewer = await make_user(s, email="ro@x.io", password="pw12345-secure")
        await make_membership(s, user_id=viewer.id, tenant_id=t.id, role="read_only")
        await s.commit()
        return t.id


async def _insert_device(db_engine, tid, name="fw1"):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id, :t, :n, 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tid, "n": name},
        )
        await s.commit()
    return did


async def _seed_events(db_engine, tid, did, src_ips):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    base = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
    async with factory() as s:
        for i, src in enumerate(src_ips):
            await s.execute(
                text(
                    "INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip, dst_ip) "
                    "VALUES (:t, :d, 'ids', :k, :tid, 'SIG', :src, '8.8.8.8')"
                ),
                {"t": base + timedelta(minutes=i), "d": did, "k": f"k{i}", "tid": tid, "src": src},
            )
        await s.commit()
    return base


async def _login(client, email):
    await client.post("/api/login", json={"email": email, "password": "pw12345-secure"})


def _range_params(base):
    return {
        "frm": (base - timedelta(hours=1)).isoformat(),
        "to": (base + timedelta(hours=1)).isoformat(),
    }


async def test_returns_country_list(api_client, db_engine, monkeypatch):
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)
    base = await _seed_events(db_engine, tid, did, ["77.88.8.8", "77.88.8.8", "8.8.8.8"])
    _patch_get_geoip(monkeypatch, _fixture_geoip)

    await _login(api_client, "ta@x.io")
    r = await api_client.get(f"/api/tenants/{tid}/attacker-countries", params=_range_params(base))
    assert r.status_code == 200
    body = r.json()
    by_code = {row["code"]: row["count"] for row in body}
    assert by_code == {"RU": 2, "US": 1}
    assert round(sum(row["pct"] for row in body)) == 100


async def test_read_only_may_view(api_client, db_engine, monkeypatch):
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)
    base = await _seed_events(db_engine, tid, did, ["8.8.8.8"])
    _patch_get_geoip(monkeypatch, _fixture_geoip)

    await _login(api_client, "ro@x.io")
    r = await api_client.get(f"/api/tenants/{tid}/attacker-countries", params=_range_params(base))
    assert r.status_code == 200
    assert [row["code"] for row in r.json()] == ["US"]


async def test_degrades_to_empty_without_mmdb(api_client, db_engine, monkeypatch):
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)
    base = await _seed_events(db_engine, tid, did, ["77.88.8.8"])
    # No mmdb cached / fetchable -> provider returns None -> endpoint returns [].
    _patch_get_geoip(monkeypatch, None)

    await _login(api_client, "ta@x.io")
    r = await api_client.get(f"/api/tenants/{tid}/attacker-countries", params=_range_params(base))
    assert r.status_code == 200
    assert r.json() == []


async def test_requires_auth(api_client, db_engine):
    tid = await _seed_members(db_engine)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as anon:
        r = await anon.get(f"/api/tenants/{tid}/attacker-countries")
    assert r.status_code == 401
