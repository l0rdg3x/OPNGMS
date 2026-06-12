from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.conftest import csrf_headers
from tests.factories import make_user


async def _seed(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await make_user(s, email="sa@x.io", password="pw12345-secure", is_superadmin=True)
        await make_user(s, email="reg@x.io", password="pw12345-secure")
        await s.commit()


async def _login(api_client, email):
    r = await api_client.post("/api/login", json={"email": email, "password": "pw12345-secure"})
    assert r.status_code == 200, r.text


async def test_get_hides_password_and_put_roundtrips(api_client, db_engine):
    await _seed(db_engine)
    await _login(api_client, "sa@x.io")
    g = await api_client.get("/api/admin/smtp")
    assert g.status_code == 200
    assert g.json()["has_password"] is False
    assert "password" not in g.json()
    p = await api_client.put("/api/admin/smtp", headers=csrf_headers(api_client), json={
        "enabled": True, "host": "smtp.x.io", "port": 587, "security": "starttls",
        "username": "u", "from_email": "noc@x.io", "from_name": "NOC", "password": "secret-12chr!",
    })
    assert p.status_code == 200, p.text
    g2 = await api_client.get("/api/admin/smtp")
    assert g2.json()["host"] == "smtp.x.io"
    assert g2.json()["has_password"] is True
    assert "password" not in g2.json()


async def test_non_superadmin_denied(api_client, db_engine):
    await _seed(db_engine)
    await _login(api_client, "reg@x.io")
    assert (await api_client.get("/api/admin/smtp")).status_code == 403


async def test_smtp_test_uses_submitted_config(api_client, db_engine, monkeypatch):
    import app.api.smtp as smtp_api

    sent = {}

    async def fake_send(cfg, **kw):
        sent["cfg"] = cfg
        sent["recipients"] = kw["recipients"]

    monkeypatch.setattr(smtp_api, "send_report_email", fake_send)
    await _seed(db_engine)
    await _login(api_client, "sa@x.io")
    r = await api_client.post("/api/admin/smtp/test", headers=csrf_headers(api_client), json={
        "to": "ops@x.io", "host": "smtp.x.io", "port": 587, "security": "starttls",
        "username": "u", "from_email": "noc@x.io", "from_name": "NOC", "password": "secret-12chr!",
    })
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert sent["recipients"] == ["ops@x.io"]


async def test_smtp_test_reports_failure(api_client, db_engine, monkeypatch):
    import app.api.smtp as smtp_api
    from app.services.email.smtp import EmailSendError

    async def boom(cfg, **kw):
        raise EmailSendError("auth failed")

    monkeypatch.setattr(smtp_api, "send_report_email", boom)
    await _seed(db_engine)
    await _login(api_client, "sa@x.io")
    r = await api_client.post("/api/admin/smtp/test", headers=csrf_headers(api_client), json={
        "to": "ops@x.io", "host": "h", "port": 587, "security": "starttls",
        "username": None, "from_email": "noc@x.io", "from_name": "NOC", "password": None,
    })
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "auth failed" in r.json()["detail"]
