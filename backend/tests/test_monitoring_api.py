import uuid
from datetime import datetime, timezone

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.main import app
from tests.factories import make_tenant, make_user


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


async def _insert_device(db_engine, tenant_id, name="fw1", status="reachable"):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices "
                "(id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id, :t, :n, 'https://x', ''::bytea, ''::bytea, true, :st, '{}')"
            ),
            {"id": did, "t": tenant_id, "n": name, "st": status},
        )
        await s.commit()
    return did


async def test_metrics_endpoint_returns_series(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO metrics (time, device_id, metric, label, tenant_id, value) "
                "VALUES (:t, :d, 'cpu.load', '', :tid, 42.0)"
            ),
            {"t": datetime.now(timezone.utc), "d": did, "tid": tid},
        )
        await s.commit()
    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/metrics", params={"metric": "cpu.load"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["metric"] == "cpu.load"
    assert body["points"][0]["value"] == 42.0
    assert body["last"][0]["value"] == 42.0


async def test_metrics_naive_from_does_not_500(api_client, db_engine):
    """A naive `from` (without Z/offset) must not cause a 500.

    Pydantic v2 produces a naive datetime; comparing it with `now` (tz-aware)
    would raise TypeError -> HTTP 500. The fix normalizes naive values to UTC.
    Expected 200; with a naive `from` that precedes the (recent) seeded metric,
    the series must include the point.
    """
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO metrics (time, device_id, metric, label, tenant_id, value) "
                "VALUES (:t, :d, 'cpu.load', '', :tid, 42.0)"
            ),
            {"t": datetime.now(timezone.utc), "d": did, "tid": tid},
        )
        await s.commit()
    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/metrics",
        params={"metric": "cpu.load", "from": "2026-01-01T00:00:00"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["metric"] == "cpu.load"
    # naive `from` (2026-01-01, well before the recent metric) -> point included.
    assert body["points"][0]["value"] == 42.0


async def test_health_endpoint_counts(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    await _insert_device(db_engine, tid, name="fw1", status="reachable")
    await _insert_device(db_engine, tid, name="fw2", status="unverified")
    r = await api_client.get(f"/api/tenants/{tid}/health")
    assert r.status_code == 200
    body = r.json()
    assert body["total_devices"] == 2
    assert body["by_status"] == {"reachable": 1, "unverified": 1}
    assert body["active_alerts"] == 0


async def test_alerts_endpoint_active_filter(api_client, db_engine):
    """The `active` filter really discriminates: two alerts (one active, one resolved)
    on the same device. active=true -> only the active one; active=false -> full history.
    """
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        # ACTIVE alert: resolved_at NULL.
        await s.execute(
            text(
                "INSERT INTO alerts (id, tenant_id, device_id, type, label, severity, details) "
                "VALUES (:id, :tid, :did, 'device.down', '', 'critical', '{}'::jsonb)"
            ),
            {"id": uuid.uuid4(), "tid": tid, "did": did},
        )
        # RESOLVED alert: resolved_at set. Different type/label to stay outside
        # the partial unique constraint uq_alerts_active (which applies only to resolved_at NULL).
        await s.execute(
            text(
                "INSERT INTO alerts "
                "(id, tenant_id, device_id, type, label, severity, resolved_at, details) "
                "VALUES (:id, :tid, :did, 'gateway.down', 'wan', 'warning', :resolved, '{}'::jsonb)"
            ),
            {
                "id": uuid.uuid4(),
                "tid": tid,
                "did": did,
                "resolved": datetime.now(timezone.utc),
            },
        )
        await s.commit()

    # active=true -> ONLY the active alert (the resolved one is excluded).
    r = await api_client.get(f"/api/tenants/{tid}/alerts", params={"active": "true"})
    assert r.status_code == 200
    assert [a["type"] for a in r.json()] == ["device.down"]

    # active=false -> BOTH (full history: active + resolved).
    r = await api_client.get(f"/api/tenants/{tid}/alerts", params={"active": "false"})
    assert r.status_code == 200
    assert {a["type"] for a in r.json()} == {"device.down", "gateway.down"}

    # default (no parameter) -> active=true -> only the active one.
    r = await api_client.get(f"/api/tenants/{tid}/alerts")
    assert r.status_code == 200
    assert [a["type"] for a in r.json()] == ["device.down"]


async def test_metrics_requires_auth(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)
    # new client without a session cookie
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as anon:
        r = await anon.get(
            f"/api/tenants/{tid}/devices/{did}/metrics", params={"metric": "cpu.load"}
        )
    assert r.status_code == 401


# --- DoS guards of the metrics endpoint (Task 2) ---


async def test_metrics_rejects_inverted_range(api_client, db_engine):
    """from >= to must be rejected with 400 (invalid interval)."""
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)
    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/metrics",
        params={
            "metric": "cpu.load",
            "from": "2026-06-09T00:00:00Z",
            "to": "2026-06-08T00:00:00Z",
        },
    )
    assert r.status_code == 400


async def test_metrics_rejects_too_many_points(api_client, db_engine):
    """A very wide range with bucket=1s exceeds MAX_POINTS -> 400."""
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)
    # 30 days with bucket 1s => (to-from)/bucket ~= 2.6M points, well beyond MAX_POINTS (5000).
    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/metrics",
        params={
            "metric": "cpu.load",
            "from": "2026-05-01T00:00:00Z",
            "to": "2026-05-31T00:00:00Z",
            "bucket": 1,
        },
    )
    assert r.status_code == 400


# --- Negative RBAC: user without membership on the tenant -> 403 ---


async def test_monitoring_forbidden_without_membership(api_client, db_engine):
    """A non-superadmin user without a membership on the tenant gets a 403.

    All tenant roles have DEVICE_VIEW, so the only realistic 403 is the
    absence of a membership: tenant_context fails with "Tenant access denied".
    """
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        # first user (superadmin) created directly so /api/setup is blocked;
        # the user under test is non-superadmin and without a membership on this tenant.
        await make_user(s, email="other@x.io", password="pw12345-secure", is_superadmin=False)
        await s.commit()
        tid = t.id
    # new client to avoid reusing any cookies
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as c:
        login = await c.post(
            "/api/login", json={"email": "other@x.io", "password": "pw12345-secure"}
        )
        assert login.status_code == 200
        r_health = await c.get(f"/api/tenants/{tid}/health")
        r_alerts = await c.get(f"/api/tenants/{tid}/alerts")
    assert r_health.status_code == 403
    assert r_alerts.status_code == 403
