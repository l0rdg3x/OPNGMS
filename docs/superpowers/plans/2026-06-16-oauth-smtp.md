# OAuth SMTP (PR1 — core + manual entry) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the global SMTP relay authenticate with OAuth2 (SASL XOAUTH2) for Gmail + Microsoft 365 — admin pastes client_id/secret/refresh_token; OPNGMS refreshes an access token at send time and `AUTH XOAUTH2`. Password auth keeps working.

**Architecture:** Extend the `SmtpSettings` singleton with OAuth fields (secrets Fernet-encrypted). A new `app/services/email/oauth.py` fetches an access token from the provider's fixed token endpoint. `send_email` gains an XOAUTH2 branch via `aiosmtplib`'s native `auth_xoauth2`. `SmtpSettingsService.to_send_config` becomes an **async** `resolve_send_config` that resolves the access token for oauth.

**Tech Stack:** Python 3.14 / SQLAlchemy / Alembic / aiosmtplib 5.1.1 (native `auth_xoauth2`) / httpx / pytest + respx. React/TS frontend. Spec: `docs/superpowers/specs/2026-06-16-oauth-smtp-design.md`.

---

## File structure

| File | Change |
|------|--------|
| `backend/app/models/smtp_settings.py` | + 6 oauth columns |
| `backend/migrations/versions/0043_smtp_oauth.py` | new migration (down_revision `0042`) |
| `backend/app/services/email/oauth.py` | new — `fetch_access_token` |
| `backend/app/services/email/smtp.py` | `SmtpSendConfig.access_token` + XOAUTH2 branch in `send_email` |
| `backend/app/services/smtp_settings.py` | `upsert` oauth params; `to_send_config` → async `resolve_send_config` |
| `backend/app/worker.py` | 2 call sites → `await resolve_send_config` |
| `backend/app/schemas/smtp.py` | `SmtpSettingsIn`/`SmtpSettingsOut` oauth fields |
| `backend/app/api/smtp.py` | `_out` oauth flags; PUT passes oauth; `/test` resolves oauth |
| `backend/tests/test_email_oauth.py`, `test_smtp_xoauth2.py`, `test_smtp_settings_service.py`, `test_smtp_api.py` | new/extended |
| `frontend/src/pages/SmtpSettingsPage.tsx`, `admin/smtpHooks.ts`, `i18n/*.ts`, `pages/__tests__/smtpSettings.test.tsx` | OAuth form + i18n |

---

## Task 1: Model columns + migration

**Files:** Modify `backend/app/models/smtp_settings.py`; Create `backend/migrations/versions/0043_smtp_oauth.py`; Test `backend/tests/test_smtp_oauth_migration.py`.

- [ ] **Step 1: Write the failing test**
```python
# backend/tests/test_smtp_oauth_migration.py
from sqlalchemy import text


async def test_smtp_settings_has_oauth_columns(db_engine):
    async with db_engine.begin() as conn:
        cols = set((await conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='smtp_settings'"
        ))).scalars().all())
    assert {"auth_method", "oauth_provider", "oauth_client_id",
            "oauth_client_secret_enc", "oauth_refresh_token_enc", "oauth_tenant_id"} <= cols
```

- [ ] **Step 2: Run to verify it fails** — `cd backend && python -m pytest tests/test_smtp_oauth_migration.py -q` (FAIL: columns absent).

- [ ] **Step 3: Add the model fields.** In `app/models/smtp_settings.py`, after `password_enc` (line 26), add:
```python
    auth_method: Mapped[str] = mapped_column(String, default="password", server_default="password")
    oauth_provider: Mapped[str | None] = mapped_column(String, nullable=True)
    oauth_client_id: Mapped[str | None] = mapped_column(String, nullable=True)
    oauth_client_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    oauth_refresh_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    oauth_tenant_id: Mapped[str | None] = mapped_column(String, nullable=True)
```

- [ ] **Step 4: Create the migration** `app/../migrations/versions/0043_smtp_oauth.py` (use the repo's migrations dir — same place as `0042_device_mgmt_source_ip.py`):
```python
"""smtp_settings OAuth columns"""

import sqlalchemy as sa
from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("smtp_settings", sa.Column(
        "auth_method", sa.String(), nullable=False, server_default="password"))
    op.add_column("smtp_settings", sa.Column("oauth_provider", sa.String(), nullable=True))
    op.add_column("smtp_settings", sa.Column("oauth_client_id", sa.String(), nullable=True))
    op.add_column("smtp_settings", sa.Column("oauth_client_secret_enc", sa.LargeBinary(), nullable=True))
    op.add_column("smtp_settings", sa.Column("oauth_refresh_token_enc", sa.LargeBinary(), nullable=True))
    op.add_column("smtp_settings", sa.Column("oauth_tenant_id", sa.String(), nullable=True))


def downgrade() -> None:
    for c in ("oauth_tenant_id", "oauth_refresh_token_enc", "oauth_client_secret_enc",
              "oauth_client_id", "oauth_provider", "auth_method"):
        op.drop_column("smtp_settings", c)
```

- [ ] **Step 5: Run to verify it passes** (the test DB builds schema from metadata via `create_all`) — `python -m pytest tests/test_smtp_oauth_migration.py -q` (PASS).

- [ ] **Step 6: Commit** — `git add app/models/smtp_settings.py migrations/versions/0043_smtp_oauth.py tests/test_smtp_oauth_migration.py && git commit -m "feat(smtp): oauth columns on smtp_settings + migration 0043"`

---

## Task 2: `fetch_access_token` token service

**Files:** Create `backend/app/services/email/oauth.py`; Test `backend/tests/test_email_oauth.py`.

- [ ] **Step 1: Write the failing tests**
```python
# backend/tests/test_email_oauth.py
import httpx
import pytest
import respx

from app.services.email.oauth import OAuthTokenError, fetch_access_token


@respx.mock
async def test_google_refresh_returns_access_token():
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(200, json={"access_token": "ya29.tok", "expires_in": 3599}))
    tok = await fetch_access_token("google", "cid", "secret", "refresh")
    assert tok == "ya29.tok"


@respx.mock
async def test_microsoft_uses_tenant_in_url():
    route = respx.post("https://login.microsoftonline.com/my-tenant/oauth2/v2.0/token").mock(
        return_value=httpx.Response(200, json={"access_token": "ms.tok"}))
    tok = await fetch_access_token("microsoft", "cid", "secret", "refresh", tenant_id="my-tenant")
    assert tok == "ms.tok" and route.called


@respx.mock
async def test_microsoft_defaults_tenant_common():
    route = respx.post("https://login.microsoftonline.com/common/oauth2/v2.0/token").mock(
        return_value=httpx.Response(200, json={"access_token": "ms.tok"}))
    await fetch_access_token("microsoft", "cid", "secret", "refresh")
    assert route.called


@respx.mock
async def test_error_response_raises_oauth_token_error():
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(OAuthTokenError):
        await fetch_access_token("google", "cid", "secret", "refresh")


async def test_unknown_provider_raises():
    with pytest.raises(OAuthTokenError):
        await fetch_access_token("yahoo", "cid", "secret", "refresh")
```

- [ ] **Step 2: Run to verify it fails** — `python -m pytest tests/test_email_oauth.py -q` (ImportError).

- [ ] **Step 3: Implement** `app/services/email/oauth.py`:
```python
"""Exchange a stored OAuth2 refresh token for a short-lived access token, for SMTP XOAUTH2.

The token endpoints are FIXED per-provider constants (no user-controlled host), so this adds no
outbound SSRF surface. The access token is returned in memory only and is never logged.
"""
from __future__ import annotations

import httpx

_TOKEN_URL = {
    "google": "https://oauth2.googleapis.com/token",
    "microsoft": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
}
# Scope echoed on the refresh grant (Microsoft wants it; Google ignores an extra scope harmlessly).
_SCOPE = {
    "google": "https://mail.google.com/",
    "microsoft": "https://outlook.office365.com/SMTP.Send offline_access",
}
_TIMEOUT = 15.0


class OAuthTokenError(Exception):
    """A refresh-token exchange failed. Message is safe to surface; carries no token material."""


async def fetch_access_token(
    provider: str, client_id: str, client_secret: str, refresh_token: str,
    tenant_id: str | None = None,
) -> str:
    if provider not in _TOKEN_URL:
        raise OAuthTokenError(f"unsupported oauth provider: {provider}")
    url = _TOKEN_URL[provider].format(tenant=tenant_id or "common")
    data = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "scope": _SCOPE[provider],
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as http:
            resp = await http.post(url, data=data)
    except httpx.HTTPError as exc:
        raise OAuthTokenError("token endpoint unreachable") from exc
    if resp.status_code != 200:
        # Body may name the error class (e.g. invalid_grant) but never the token.
        raise OAuthTokenError(f"token exchange failed ({resp.status_code})")
    token = resp.json().get("access_token")
    if not token:
        raise OAuthTokenError("no access_token in response")
    return token
```

- [ ] **Step 4: Run to verify it passes** — `python -m pytest tests/test_email_oauth.py -q` (5 PASS).

- [ ] **Step 5: Commit** — `git add app/services/email/oauth.py tests/test_email_oauth.py && git commit -m "feat(smtp): oauth access-token fetch (google/microsoft refresh grant)"`

---

## Task 3: XOAUTH2 send path

**Files:** Modify `backend/app/services/email/smtp.py`; Test `backend/tests/test_smtp_xoauth2.py`.

- [ ] **Step 1: Write the failing test** (mock the aiosmtplib client so no real SMTP is needed)
```python
# backend/tests/test_smtp_xoauth2.py
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.email.smtp import SmtpSendConfig, send_email


def _cfg(**kw):
    base = dict(host="smtp.gmail.com", port=587, security="starttls", username="me@x.com",
               password=None, from_email="me@x.com", from_name="Me")
    base.update(kw)
    return SmtpSendConfig(**base)


async def test_send_uses_xoauth2_when_access_token_set():
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.auth_xoauth2 = AsyncMock()
    client.login = AsyncMock()
    client.send_message = AsyncMock()
    with patch("app.services.email.smtp.aiosmtplib.SMTP", return_value=client):
        await send_email(_cfg(access_token="ya29.tok"), subject="s", recipients=["to@x.com"],
                         body_text="b")
    client.auth_xoauth2.assert_awaited_once_with("me@x.com", "ya29.tok")
    client.login.assert_not_awaited()
    client.send_message.assert_awaited_once()
```

- [ ] **Step 2: Run to verify it fails** — `python -m pytest tests/test_smtp_xoauth2.py -q` (FAIL: `SmtpSendConfig` has no `access_token`).

- [ ] **Step 3: Implement.** In `app/services/email/smtp.py`:
  (a) add `access_token: str | None = None` to the `SmtpSendConfig` dataclass (after `password`).
  (b) replace the body of `send_email` (the `kwargs`/`aiosmtplib.send` block, lines ~74-86) with:
```python
    client = aiosmtplib.SMTP(
        hostname=cfg.host, port=cfg.port,
        start_tls=cfg.security == "starttls", use_tls=cfg.security == "tls",
    )
    try:
        async with client:
            if cfg.access_token:
                await client.auth_xoauth2(cfg.username or cfg.from_email, cfg.access_token)
            elif cfg.username:
                await client.login(cfg.username, cfg.password or "")
            await client.send_message(message)
    except (aiosmtplib.SMTPException, OSError) as exc:
        raise EmailSendError(_safe_smtp_error(exc)) from exc
```
(`async with client` opens the connection, performs the STARTTLS/implicit-TLS handshake, and quits on exit. Keep the existing imports + `_build_message`/`_safe_smtp_error`.)

- [ ] **Step 4: Run to verify it passes** — `python -m pytest tests/test_smtp_xoauth2.py -q` (PASS). Then run the existing SMTP tests to confirm the password path still works: `python -m pytest tests/ -q -k "smtp or email"`.

- [ ] **Step 5: Commit** — `git add app/services/email/smtp.py tests/test_smtp_xoauth2.py && git commit -m "feat(smtp): XOAUTH2 send branch via aiosmtplib.auth_xoauth2"`

---

## Task 4: Settings service — oauth upsert + async `resolve_send_config`

**Files:** Modify `backend/app/services/smtp_settings.py`, `backend/app/worker.py`; Test `backend/tests/test_smtp_settings_service.py`.

- [ ] **Step 1: Write the failing tests**
```python
# backend/tests/test_smtp_settings_service.py
from unittest.mock import AsyncMock, patch

from app.services.smtp_settings import SmtpSettingsService


async def _svc(db_session):
    return SmtpSettingsService(db_session)


async def test_oauth_upsert_encrypts_secrets_and_keeps_on_blank(db_session):
    svc = await _svc(db_session)
    row = await svc.upsert(
        enabled=True, host="smtp.gmail.com", port=587, security="starttls", username=None,
        from_email="me@x.com", from_name="Me", password=None, clear_password=False,
        auth_method="oauth", oauth_provider="google", oauth_client_id="cid",
        oauth_client_secret="secret", oauth_refresh_token="refresh", oauth_tenant_id=None,
        clear_client_secret=False, clear_refresh_token=False,
    )
    assert row.auth_method == "oauth" and row.oauth_client_secret_enc and row.oauth_refresh_token_enc
    enc1 = row.oauth_refresh_token_enc
    # Blank secret + not-clear -> keep existing.
    row = await svc.upsert(
        enabled=True, host="smtp.gmail.com", port=587, security="starttls", username=None,
        from_email="me@x.com", from_name="Me", password=None, clear_password=False,
        auth_method="oauth", oauth_provider="google", oauth_client_id="cid",
        oauth_client_secret=None, oauth_refresh_token=None, oauth_tenant_id=None,
        clear_client_secret=False, clear_refresh_token=False,
    )
    assert row.oauth_refresh_token_enc == enc1


async def test_resolve_send_config_oauth_fetches_access_token(db_session):
    svc = await _svc(db_session)
    row = await svc.upsert(
        enabled=True, host="smtp.gmail.com", port=587, security="starttls", username=None,
        from_email="me@x.com", from_name="Me", password=None, clear_password=False,
        auth_method="oauth", oauth_provider="google", oauth_client_id="cid",
        oauth_client_secret="secret", oauth_refresh_token="refresh", oauth_tenant_id=None,
        clear_client_secret=False, clear_refresh_token=False,
    )
    with patch("app.services.smtp_settings.fetch_access_token",
               AsyncMock(return_value="ya29.tok")) as m:
        cfg = await svc.resolve_send_config(row)
    assert cfg.access_token == "ya29.tok" and cfg.password is None and cfg.username == "me@x.com"
    m.assert_awaited_once_with("google", "cid", "secret", "refresh", "")


async def test_resolve_send_config_password_unchanged(db_session):
    svc = await _svc(db_session)
    row = await svc.upsert(
        enabled=True, host="smtp.x.com", port=587, security="starttls", username="u",
        from_email="me@x.com", from_name="Me", password="pw", clear_password=False,
        auth_method="password", oauth_provider=None, oauth_client_id=None,
        oauth_client_secret=None, oauth_refresh_token=None, oauth_tenant_id=None,
        clear_client_secret=False, clear_refresh_token=False,
    )
    cfg = await svc.resolve_send_config(row)
    assert cfg.password == "pw" and cfg.access_token is None
```
> Note: confirm the `db_session` fixture name in `backend/tests/conftest.py` (use whatever the existing service tests use — an async session bound to the test DB). If the project uses `async_sessionmaker(db_engine)` directly in tests, mirror that pattern instead.

- [ ] **Step 2: Run to verify it fails** — `python -m pytest tests/test_smtp_settings_service.py -q` (FAIL: `upsert` got unexpected kwargs / no `resolve_send_config`).

- [ ] **Step 3: Implement.** In `app/services/smtp_settings.py`:
  (a) add the import: `from app.services.email.oauth import fetch_access_token`.
  (b) extend `upsert` signature with the oauth params and persist them (keep/clear convention):
```python
    async def upsert(self, *, enabled: bool, host: str, port: int, security: str,
                     username: str | None, from_email: str, from_name: str,
                     password: str | None, clear_password: bool,
                     auth_method: str = "password", oauth_provider: str | None = None,
                     oauth_client_id: str | None = None, oauth_client_secret: str | None = None,
                     oauth_refresh_token: str | None = None, oauth_tenant_id: str | None = None,
                     clear_client_secret: bool = False,
                     clear_refresh_token: bool = False) -> SmtpSettings:
        row = await self.get()
        if row is None:
            row = SmtpSettings(id=SINGLETON_ID)
            self.session.add(row)
        row.enabled, row.host, row.port, row.security = enabled, host, port, security
        row.username = username or None
        row.from_email, row.from_name = from_email, from_name
        if clear_password:
            row.password_enc = None
        elif password:
            row.password_enc = crypto.encrypt(password)
        row.auth_method = auth_method
        row.oauth_provider = oauth_provider or None
        row.oauth_client_id = oauth_client_id or None
        row.oauth_tenant_id = oauth_tenant_id or None
        if clear_client_secret:
            row.oauth_client_secret_enc = None
        elif oauth_client_secret:
            row.oauth_client_secret_enc = crypto.encrypt(oauth_client_secret)
        if clear_refresh_token:
            row.oauth_refresh_token_enc = None
        elif oauth_refresh_token:
            row.oauth_refresh_token_enc = crypto.encrypt(oauth_refresh_token)
        await self.session.flush()
        return row
```
  (c) **replace** `to_send_config` with the async `resolve_send_config`:
```python
    async def resolve_send_config(self, row: SmtpSettings) -> SmtpSendConfig:
        if row.auth_method == "oauth":
            token = await fetch_access_token(
                row.oauth_provider or "", row.oauth_client_id or "",
                crypto.decrypt(row.oauth_client_secret_enc) if row.oauth_client_secret_enc else "",
                crypto.decrypt(row.oauth_refresh_token_enc) if row.oauth_refresh_token_enc else "",
                row.oauth_tenant_id or "",
            )
            return SmtpSendConfig(
                host=row.host, port=row.port, security=row.security,
                username=row.from_email, password=None, access_token=token,
                from_email=row.from_email, from_name=row.from_name,
            )
        return SmtpSendConfig(
            host=row.host, port=row.port, security=row.security, username=row.username,
            password=crypto.decrypt(row.password_enc) if row.password_enc else None,
            from_email=row.from_email, from_name=row.from_name,
        )
```

- [ ] **Step 4: Update the worker call sites.** In `app/worker.py` line ~430 and ~481, change `svc.to_send_config(smtp)` → `await svc.resolve_send_config(smtp)` (both are already in async functions). Wrap each in the existing error handling — if a `resolve_send_config` raises `OAuthTokenError`, the surrounding report-send should treat it like a send failure (log + skip), consistent with how `EmailSendError` is handled there; read the surrounding lines and mirror that try/except (import `OAuthTokenError` from `app.services.email.oauth` if you add a catch). If the code already wraps the send in a broad `except Exception`, no extra catch is needed.

- [ ] **Step 5: Run to verify it passes** — `python -m pytest tests/test_smtp_settings_service.py -q` (PASS) + `python -m pytest tests/ -q -k "smtp or report or worker"` (the worker/report tests still green).

- [ ] **Step 6: Commit** — `git add app/services/smtp_settings.py app/worker.py tests/test_smtp_settings_service.py && git commit -m "feat(smtp): async resolve_send_config + oauth upsert (3 call sites)"`

---

## Task 5: Schemas + API

**Files:** Modify `backend/app/schemas/smtp.py`, `backend/app/api/smtp.py`; Test `backend/tests/test_smtp_api.py` (extend or create).

- [ ] **Step 1: Write the failing test** (find the existing SMTP API test; if none, create `tests/test_smtp_api.py` — use the project's `client`/`superadmin` fixtures, mirroring another admin-endpoint test like `tests/test_audit_api.py`)
```python
async def test_put_oauth_then_get_exposes_flags_not_secrets(client_superadmin):
    body = {
        "enabled": True, "host": "smtp.gmail.com", "port": 587, "security": "starttls",
        "from_email": "me@x.com", "from_name": "Me",
        "auth_method": "oauth", "oauth_provider": "google", "oauth_client_id": "cid",
        "oauth_client_secret": "secret", "oauth_refresh_token": "refresh",
    }
    r = await client_superadmin.put("/api/admin/smtp", json=body)
    assert r.status_code == 200
    out = (await client_superadmin.get("/api/admin/smtp")).json()
    assert out["auth_method"] == "oauth" and out["oauth_provider"] == "google"
    assert out["oauth_client_id"] == "cid"
    assert out["has_client_secret"] is True and out["has_refresh_token"] is True
    # Secrets are NEVER serialized.
    assert "oauth_client_secret" not in out and "oauth_refresh_token" not in out
```
> Note: use the same authenticated-superadmin client fixture the other `app/api/admin/*` tests use; if the suite posts a CSRF header, include it as those tests do.

- [ ] **Step 2: Run to verify it fails** — the PUT 422s (unknown fields) or GET lacks the flags.

- [ ] **Step 3: Implement schemas.** In `app/schemas/smtp.py`:
  - `SmtpSettingsIn`: add
    ```python
    auth_method: str = "password"
    oauth_provider: str | None = Field(default=None, max_length=32)
    oauth_client_id: str | None = Field(default=None, max_length=512)
    oauth_client_secret: str | None = Field(default=None, max_length=2048)
    oauth_refresh_token: str | None = Field(default=None, max_length=4096)
    oauth_tenant_id: str | None = Field(default=None, max_length=128)
    clear_client_secret: bool = False
    clear_refresh_token: bool = False
    ```
  - `SmtpSettingsOut`: add
    ```python
    auth_method: str
    oauth_provider: str | None
    oauth_client_id: str | None
    oauth_tenant_id: str | None
    has_client_secret: bool
    has_refresh_token: bool
    ```

- [ ] **Step 4: Implement API.** In `app/api/smtp.py`:
  - `_out(row)`: for the `None` case add `auth_method="password", oauth_provider=None, oauth_client_id=None, oauth_tenant_id=None, has_client_secret=False, has_refresh_token=False`; for the row case add `auth_method=row.auth_method, oauth_provider=row.oauth_provider, oauth_client_id=row.oauth_client_id, oauth_tenant_id=row.oauth_tenant_id, has_client_secret=row.oauth_client_secret_enc is not None, has_refresh_token=row.oauth_refresh_token_enc is not None`.
  - `put_smtp`: pass the oauth params through to `svc.upsert(...)` (the new kwargs: `auth_method=body.auth_method, oauth_provider=body.oauth_provider, oauth_client_id=body.oauth_client_id, oauth_client_secret=body.oauth_client_secret, oauth_refresh_token=body.oauth_refresh_token, oauth_tenant_id=body.oauth_tenant_id, clear_client_secret=body.clear_client_secret, clear_refresh_token=body.clear_refresh_token`).
  - `test_smtp`: replace the password-only config build with a unified resolve. After validating security, build the send config:
    ```python
    svc = SmtpSettingsService(session)
    stored = await svc.get()
    if body.auth_method == "oauth":
        from app.services.email.oauth import OAuthTokenError, fetch_access_token
        secret = body.oauth_client_secret or (
            crypto.decrypt(stored.oauth_client_secret_enc)
            if stored and stored.oauth_client_secret_enc else "")
        refresh = body.oauth_refresh_token or (
            crypto.decrypt(stored.oauth_refresh_token_enc)
            if stored and stored.oauth_refresh_token_enc else "")
        try:
            token = await fetch_access_token(body.oauth_provider or "", body.oauth_client_id or "",
                                             secret, refresh, body.oauth_tenant_id or "")
        except OAuthTokenError as exc:
            await session.commit()
            return SmtpTestOut(ok=False, detail=str(exc))
        cfg = SmtpSendConfig(host=body.host, port=body.port, security=body.security,
                             username=str(body.from_email), password=None, access_token=token,
                             from_email=str(body.from_email), from_name=body.from_name)
    else:
        password = body.password
        if password is None and stored and stored.password_enc:
            password = crypto.decrypt(stored.password_enc)
        cfg = SmtpSendConfig(host=body.host, port=body.port, security=body.security,
                             username=body.username, password=password,
                             from_email=str(body.from_email), from_name=body.from_name)
    ```
    (add `from app.core import crypto` at the top of `api/smtp.py`; keep the audit record + commit + the existing `try/except EmailSendError` send block.)

- [ ] **Step 5: Run to verify it passes** — `python -m pytest tests/test_smtp_api.py -q` (PASS).

- [ ] **Step 6: Commit** — `git add app/schemas/smtp.py app/api/smtp.py tests/test_smtp_api.py && git commit -m "feat(smtp): oauth fields in schema + API (flags only, no secrets); oauth test-send"`

---

## Task 6: Frontend OAuth form + i18n

**Files:** Modify `frontend/src/pages/SmtpSettingsPage.tsx`, `frontend/src/admin/smtpHooks.ts`, `frontend/src/i18n/en.ts` (+ 11 locales), `frontend/src/pages/__tests__/smtpSettings.test.tsx`.

- [ ] **Step 1: Read the current page + hooks + test** to match patterns (`SmtpSettingsPage.tsx`, `smtpHooks.ts`, `smtpSettings.test.tsx`). Regenerate the typed API client first so the new fields exist: `cd frontend && npm run gen:api` (requires the backend importable; if it fails in this environment, hand-edit `src/api/schema.d.ts`'s SMTP request/response types to add the new fields, matching the backend schema).

- [ ] **Step 2: Add i18n keys.** In `frontend/src/i18n/en.ts`, under the `smtp` section, add:
```ts
    authMethod: "Authentication",
    authPassword: "Password",
    authOauth: "OAuth2",
    oauthProvider: "Provider",
    oauthGoogle: "Google / Gmail",
    oauthMicrosoft: "Microsoft 365",
    oauthClientId: "Client ID",
    oauthClientSecret: "Client secret",
    oauthRefreshToken: "Refresh token",
    oauthTenantId: "Tenant ID",
    oauthSecretSaved: "saved — leave blank to keep",
```
Mirror these in all 11 sibling locales (`it es fr de pt nl ru ar zh zhTW ja`). `Client ID`/`OAuth2`/`Google`/`Microsoft 365`/`Tenant ID` are product/proper terms — keep them as-is across locales; translate the descriptive labels. Suggested translations for `authMethod`/`authPassword`/`oauthProvider`/`oauthClientSecret`/`oauthRefreshToken`/`oauthSecretSaved`:
- it: "Autenticazione" / "Password" / "Provider" / "Client secret" / "Refresh token" / "salvato — lascia vuoto per mantenere"
- es: "Autenticación" / "Contraseña" / "Proveedor" / "Client secret" / "Refresh token" / "guardado — déjalo en blanco para mantener"
- fr: "Authentification" / "Mot de passe" / "Fournisseur" / "Client secret" / "Refresh token" / "enregistré — laisser vide pour conserver"
- de: "Authentifizierung" / "Passwort" / "Anbieter" / "Client Secret" / "Refresh Token" / "gespeichert — leer lassen zum Beibehalten"
- pt: "Autenticação" / "Senha" / "Provedor" / "Client secret" / "Refresh token" / "salvo — deixe em branco para manter"
- nl: "Authenticatie" / "Wachtwoord" / "Provider" / "Client secret" / "Refresh token" / "opgeslagen — laat leeg om te behouden"
- ru: "Аутентификация" / "Пароль" / "Провайдер" / "Client secret" / "Refresh token" / "сохранено — оставьте пустым, чтобы сохранить"
- ar: "المصادقة" / "كلمة المرور" / "المزوّد" / "Client secret" / "Refresh token" / "محفوظ — اتركه فارغًا للإبقاء"
- zh: "身份验证" / "密码" / "提供商" / "Client secret" / "Refresh token" / "已保存 — 留空以保留"
- zhTW: "身分驗證" / "密碼" / "提供者" / "Client secret" / "Refresh token" / "已儲存 — 留空以保留"
- ja: "認証" / "パスワード" / "プロバイダー" / "Client secret" / "Refresh token" / "保存済み — 空欄で維持"

- [ ] **Step 3: Add the OAuth form to `SmtpSettingsPage.tsx`.** Add an Authentication `SegmentedControl` (or `Select`) bound to a new `authMethod` form field (`"password" | "oauth"`). When `oauth`: render a Provider `Select` (`google`/`microsoft`), `TextInput` Client ID, `PasswordInput` Client secret + Refresh token (placeholder = `t.smtp.oauthSecretSaved` when `has_client_secret`/`has_refresh_token`, value blank = keep), and a Tenant ID `TextInput` shown **only when provider === "microsoft"**. Submit maps these into the PUT body (`auth_method`, `oauth_provider`, `oauth_client_id`, `oauth_client_secret`, `oauth_refresh_token`, `oauth_tenant_id`; send a secret only when the field is non-empty). Hide the username/password block when `authMethod === "oauth"`. Follow the page's existing form library + the `has_password` write-only pattern already used for the password field.

- [ ] **Step 4: Update `smtpHooks.ts`** so the PUT mutation type includes the new fields and the GET type surfaces `auth_method`/`oauth_provider`/`oauth_client_id`/`oauth_tenant_id`/`has_client_secret`/`has_refresh_token` (these come from the regenerated schema; adjust any local interfaces).

- [ ] **Step 5: Add a frontend test** to `smtpSettings.test.tsx`: render the page, switch Authentication to OAuth2, select Microsoft 365 → assert the Tenant ID field appears and the Client ID/secret/refresh fields render; select Google → Tenant ID hidden. (Mock the GET to return `auth_method:"password"` initially; mirror the existing test's MSW/handler setup.)

- [ ] **Step 6: Run the gate** — `cd frontend && npm run build && npm test && npm run lint` (all green; `npm run build` = `tsc -b && vite build`, which type-checks the test too).

- [ ] **Step 7: Commit** — `git add frontend/src && git commit -m "feat(smtp): OAuth2 auth UI (provider/client/refresh fields) + i18n (12 locales)"`

---

## Task 7: Full gate

- [ ] **Step 1:** `cd backend && python -m pytest -q && ruff check app/` — all green, clean.
- [ ] **Step 2:** `cd frontend && npm run build && npm test && npm run lint` — all green.
- [ ] **Step 3: Commit** any lint fixups (only if needed): `git add -A && git commit -m "chore: lint"`.

---

## Self-review (plan vs spec)

- **Spec coverage:** model+migration (T1) ✓; token service google/microsoft+tenant+errors (T2) ✓; XOAUTH2 send via native `auth_xoauth2` (T3) ✓; async `resolve_send_config` + oauth upsert keep/clear + 3 callers (T4) ✓; schema/API flags-not-secrets + oauth test-send (T5) ✓; frontend manual OAuth form + 12-locale i18n + test (T6) ✓; security (Fernet, never returned/logged — secrets only via `crypto.encrypt`, API exposes only `has_*`) ✓; gate (T7) ✓. PR2 (Connect button) explicitly out of this plan.
- **Placeholder scan:** none — every code step is complete; the two "match the existing fixture/pattern" notes (T4 `db_session`, T5 client fixture, T6 form lib) are explicit read-the-file instructions, not vague handwaving.
- **Type/name consistency:** `auth_method`, `oauth_provider`, `oauth_client_id`, `oauth_client_secret(_enc)`, `oauth_refresh_token(_enc)`, `oauth_tenant_id`, `resolve_send_config`, `fetch_access_token`, `SmtpSendConfig.access_token`, `has_client_secret`/`has_refresh_token` used identically across tasks.
```
