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


async def test_put_oauth_then_get_exposes_flags_not_secrets(api_client, db_engine):
    await _seed(db_engine)
    await _login(api_client, "sa@x.io")
    body = {
        "enabled": True, "host": "smtp.gmail.com", "port": 587, "security": "starttls",
        "from_email": "me@x.com", "from_name": "Me",
        "auth_method": "oauth", "oauth_provider": "google", "oauth_client_id": "cid",
        "oauth_client_secret": "secret", "oauth_refresh_token": "refresh",
    }
    r = await api_client.put("/api/admin/smtp", headers=csrf_headers(api_client), json=body)
    assert r.status_code == 200, r.text
    out = (await api_client.get("/api/admin/smtp")).json()
    assert out["auth_method"] == "oauth" and out["oauth_provider"] == "google"
    assert out["oauth_client_id"] == "cid"
    assert out["has_client_secret"] is True and out["has_refresh_token"] is True
    # Secrets are NEVER serialized.
    assert "oauth_client_secret" not in out and "oauth_refresh_token" not in out


async def test_put_rejects_traversal_tenant_and_bad_auth_method(api_client, db_engine):
    await _seed(db_engine)
    await _login(api_client, "sa@x.io")
    base = {"enabled": True, "host": "smtp.office365.com", "port": 587, "security": "starttls",
            "from_email": "me@x.com", "from_name": "Me", "auth_method": "oauth",
            "oauth_provider": "microsoft", "oauth_client_id": "cid"}
    # A path-traversal tenant must not reach the token URL — rejected at the schema boundary.
    bad_tenant = await api_client.put("/api/admin/smtp", headers=csrf_headers(api_client),
                                      json={**base, "oauth_tenant_id": "common/../../users"})
    assert bad_tenant.status_code == 422, bad_tenant.text
    # A real Azure tenant GUID / "common" passes.
    ok = await api_client.put("/api/admin/smtp", headers=csrf_headers(api_client),
                              json={**base, "oauth_tenant_id": "common"})
    assert ok.status_code == 200, ok.text
    # An out-of-range auth_method is rejected (not silently treated as password).
    bad_method = await api_client.put("/api/admin/smtp", headers=csrf_headers(api_client),
                                      json={**base, "auth_method": "xoauth2"})
    assert bad_method.status_code == 422, bad_method.text


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


async def test_smtp_test_oauth_resolves_token(api_client, db_engine, monkeypatch):
    """An OAuth test-send fetches an access token and sends with it (no password)."""
    import app.api.smtp as smtp_api
    import app.services.email.oauth as oauth_mod

    sent = {}

    async def fake_send(cfg, **kw):
        sent["cfg"] = cfg

    async def fake_fetch(provider, client_id, secret, refresh, tenant_id=None):
        sent["fetch"] = (provider, client_id, secret, refresh, tenant_id)
        return "ya29.tok"

    monkeypatch.setattr(smtp_api, "send_report_email", fake_send)
    monkeypatch.setattr(oauth_mod, "fetch_access_token", fake_fetch)
    await _seed(db_engine)
    await _login(api_client, "sa@x.io")
    r = await api_client.post("/api/admin/smtp/test", headers=csrf_headers(api_client), json={
        "to": "ops@x.io", "host": "smtp.gmail.com", "port": 587, "security": "starttls",
        "from_email": "me@x.com", "from_name": "Me", "auth_method": "oauth",
        "oauth_provider": "google", "oauth_client_id": "cid",
        "oauth_client_secret": "secret", "oauth_refresh_token": "refresh",
    })
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert sent["cfg"].access_token == "ya29.tok"
    assert sent["cfg"].password is None
    assert sent["fetch"] == ("google", "cid", "secret", "refresh", "")


async def test_smtp_test_oauth_token_error_reports_failure(api_client, db_engine, monkeypatch):
    """A failed token exchange surfaces as ok=False without attempting a send."""
    import app.api.smtp as smtp_api
    import app.services.email.oauth as oauth_mod
    from app.services.email.oauth import OAuthTokenError

    async def must_not_send(cfg, **kw):
        raise AssertionError("send must not be attempted when token exchange fails")

    async def fake_fetch(*a, **kw):
        raise OAuthTokenError("token exchange failed (400)")

    monkeypatch.setattr(smtp_api, "send_report_email", must_not_send)
    monkeypatch.setattr(oauth_mod, "fetch_access_token", fake_fetch)
    await _seed(db_engine)
    await _login(api_client, "sa@x.io")
    r = await api_client.post("/api/admin/smtp/test", headers=csrf_headers(api_client), json={
        "to": "ops@x.io", "host": "smtp.gmail.com", "port": 587, "security": "starttls",
        "from_email": "me@x.com", "from_name": "Me", "auth_method": "oauth",
        "oauth_provider": "google", "oauth_client_id": "cid",
        "oauth_client_secret": "secret", "oauth_refresh_token": "refresh",
    })
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "token exchange failed" in r.json()["detail"]
