import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import make_engine, set_tenant_context
from app.core.db_roles import APP_ROLE, APP_ROLE_PASSWORD
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
    """Isolamento monitoraggio end-to-end, su tre livelli di prova:

    (a) Comportamento via API (difesa-in-profondità: gli endpoint/repository applicano
        un filtro applicativo esplicito ``WHERE tenant_id = <tenant del path>`` *e* la
        RLS gira sotto). A vede i propri dati, B i propri.
    (b) ``opngms_app`` reale legge i propri chunk dell'hypertable Timescale attraverso
        l'API (propagazione dei grant): asserzione positiva ``value == 11.0``.
    (c) E' la RLS — non il filtro applicativo — a isolare cross-tenant: a fondo test una
        query RAW *senza* ``WHERE tenant_id``, eseguita come ruolo reale ``opngms_app``
        con contesto sul tenant A, vede solo le righe di A. Senza il filtro applicativo
        l'unica difesa rimasta e' la RLS, quindi questa asserzione fallirebbe se la RLS
        fosse disattivata (non e' tautologica).

    La prova RLS pura a livello SQL e' anche in
    ``tests/test_rls_isolation.py::test_metrics_alerts_isolated_cross_tenant``.
    """
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

    # opngms_app reale legge i propri chunk dell'hypertable Timescale attraverso l'API:
    # asserzione POSITIVA -> prova la propagazione dei grant ai chunk (non e' tautologica).
    ra = await app_role_api_client.get(
        f"/api/tenants/{ta}/devices/{dev_a}/metrics", params={"metric": "cpu.load"}
    )
    assert ra.json()["points"][0]["value"] == 11.0
    # I dati di B sul device di B, interrogati nel contesto di A -> nessun punto.
    # NB: qui isola gia' il filtro applicativo WHERE tenant_id dell'endpoint, quindi questa
    # asserzione negativa NON distingue "RLS attiva" da "solo filtro applicativo": e' un
    # test di comportamento (difesa-in-profondita'). La prova che e' la RLS a isolare e'
    # la query RAW a fondo test.
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

    # Prova che e' la RLS (non il filtro applicativo) a isolare cross-tenant.
    # Sessione DIRETTA come ruolo reale opngms_app (NON via API, NON come owner),
    # contesto sul tenant A, query RAW SENZA WHERE tenant_id: l'unica difesa rimasta
    # e' la RLS. Deve vedere SOLO le righe di A -> fallirebbe se la RLS fosse spenta.
    app_url = make_url(os.environ["TEST_DATABASE_URL"]).set(
        username=APP_ROLE, password=APP_ROLE_PASSWORD
    )
    assert app_url.username == APP_ROLE  # fail loudly se il ruolo non e' stato applicato
    engine = make_engine(app_url.render_as_string(hide_password=False))
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            await set_tenant_context(s, ta)
            vals = (
                await s.execute(text("SELECT value FROM metrics ORDER BY value"))
            ).scalars().all()
            assert vals == [11.0]  # NON [11.0, 22.0]: senza filtro applicativo e' la RLS a escludere B
            n_alerts = (
                await s.execute(text("SELECT count(*) FROM alerts"))
            ).scalar_one()
            assert n_alerts == 1  # solo l'alert di A
    finally:
        await engine.dispose()
