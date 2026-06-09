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
        "/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345"}
    )
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345"})
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
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO alerts (id, tenant_id, device_id, type, label, severity, details) "
                "VALUES (:id, :tid, :did, 'device.down', '', 'critical', '{}'::jsonb)"
            ),
            {"id": uuid.uuid4(), "tid": tid, "did": did},
        )
        await s.commit()
    r = await api_client.get(f"/api/tenants/{tid}/alerts", params={"active": "true"})
    assert r.status_code == 200
    assert [a["type"] for a in r.json()] == ["device.down"]


async def test_metrics_requires_auth(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)
    # nuovo client senza cookie di sessione
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as anon:
        r = await anon.get(
            f"/api/tenants/{tid}/devices/{did}/metrics", params={"metric": "cpu.load"}
        )
    assert r.status_code == 401


# --- Guardie DoS dell'endpoint metriche (Task 2) ---


async def test_metrics_rejects_inverted_range(api_client, db_engine):
    """from >= to deve essere rifiutato con 400 (intervallo non valido)."""
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
    """Un range molto ampio con bucket=1s supera MAX_POINTS -> 400."""
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)
    # 30 giorni con bucket 1s => (to-from)/bucket ~= 2.6M punti, ben oltre MAX_POINTS (5000).
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


# --- RBAC negativo: utente senza membership sul tenant -> 403 ---


async def test_monitoring_forbidden_without_membership(api_client, db_engine):
    """Un utente non-superadmin senza membership sul tenant riceve 403.

    Tutti i ruoli tenant hanno DEVICE_VIEW, quindi l'unico 403 realistico e'
    l'assenza di membership: tenant_context fallisce con "Accesso al tenant negato".
    """
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        # primo utente (superadmin) creato direttamente cosi' /api/setup e' bloccato;
        # l'utente sotto test e' non-superadmin e senza membership su questo tenant.
        await make_user(s, email="other@x.io", password="pw12345", is_superadmin=False)
        await s.commit()
        tid = t.id
    # nuovo client per non riusare eventuali cookie
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as c:
        login = await c.post(
            "/api/login", json={"email": "other@x.io", "password": "pw12345"}
        )
        assert login.status_code == 200
        r_health = await c.get(f"/api/tenants/{tid}/health")
        r_alerts = await c.get(f"/api/tenants/{tid}/alerts")
    assert r_health.status_code == 403
    assert r_alerts.status_code == 403
