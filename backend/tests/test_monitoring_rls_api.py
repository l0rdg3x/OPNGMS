import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.main import app
from app.services.onboarding import ProbeResult, get_prober
from tests.factories import make_tenant

CSRF = {"X-OPNGMS-CSRF": "1"}


async def _setup(app_role_api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        a = await make_tenant(s, slug="a")
        b = await make_tenant(s, slug="b")
        await s.commit()
        ta, tb = a.id, b.id
    await app_role_api_client.post(
        "/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345"}
    )

    async def _fake(*ar, **kw):
        return ProbeResult(reachable=True, firmware_version="24.7", error=None)

    app.dependency_overrides[get_prober] = lambda: _fake
    await app_role_api_client.post(
        "/api/login", json={"email": "sa@x.io", "password": "pw12345"}
    )
    return ta, tb


async def _make_device(app_role_api_client, tid, name):
    r = await app_role_api_client.post(
        f"/api/tenants/{tid}/devices",
        json={"name": name, "base_url": f"https://{name}", "api_key": "k", "api_secret": "s"},
        headers=CSRF,
    )
    assert r.status_code == 201
    return uuid.UUID(r.json()["id"])


async def test_metrics_and_alerts_isolated_via_api(app_role_api_client, db_engine):
    ta, tb = await _setup(app_role_api_client, db_engine)
    dev_a = await _make_device(app_role_api_client, ta, "fw-a")
    dev_b = await _make_device(app_role_api_client, tb, "fw-b")

    # inietta dati come owner (bypassa RLS) per entrambi
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        for tid, did, val in ((ta, dev_a, 11.0), (tb, dev_b, 22.0)):
            await s.execute(
                text(
                    "INSERT INTO metrics (time, device_id, metric, label, tenant_id, value) "
                    "VALUES (:t, :d, 'cpu.load', '', :tid, :v)"
                ),
                {"t": datetime.now(timezone.utc), "d": did, "tid": tid, "v": val},
            )
            await s.execute(
                text(
                    "INSERT INTO alerts (id, tenant_id, device_id, type, label, severity, details) "
                    "VALUES (:id, :tid, :did, 'device.down', '', 'critical', '{}'::jsonb)"
                ),
                {"id": uuid.uuid4(), "tid": tid, "did": did},
            )
        await s.commit()

    # tenant A vede solo i propri dati (prova anche la propagazione RLS ai chunk Timescale)
    ra = await app_role_api_client.get(
        f"/api/tenants/{ta}/devices/{dev_a}/metrics", params={"metric": "cpu.load"}
    )
    assert ra.json()["points"][0]["value"] == 11.0
    # i dati di B sul device di B, interrogati nel contesto di A -> RLS nasconde tutto
    cross = await app_role_api_client.get(
        f"/api/tenants/{ta}/devices/{dev_b}/metrics", params={"metric": "cpu.load"}
    )
    assert cross.json()["points"] == []

    aa = await app_role_api_client.get(f"/api/tenants/{ta}/alerts")
    assert [x["device_id"] for x in aa.json()] == [str(dev_a)]
    ab = await app_role_api_client.get(f"/api/tenants/{tb}/alerts")
    assert [x["device_id"] for x in ab.json()] == [str(dev_b)]

    ha = await app_role_api_client.get(f"/api/tenants/{ta}/health")
    assert ha.json()["total_devices"] == 1
    assert ha.json()["active_alerts"] == 1
