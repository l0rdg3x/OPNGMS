# SMTP OAuth "Connect" (PR2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add a "Connect with Google / Microsoft 365" button that runs the OAuth2 authorization-code flow and stores the SMTP refresh token automatically — marked **experimental / untested** (no live-provider verification possible here).

**Architecture:** Reuse PR1's encrypted storage + token endpoints. Add an authorize-URL builder, a code→refresh-token exchange, an HMAC-signed `state`, two superadmin routes (`GET …/authorize`, `GET …/callback`), and the button. The redirect URI + landing URL are built from a new `PUBLIC_BASE_URL` config (server-side, no open-redirect).

**Tech Stack:** Python 3.14 / FastAPI / httpx / respx (test); React 19 / TS / Mantine v9 / openapi-fetch / msw+vitest.

**Spec:** `docs/superpowers/specs/2026-06-17-smtp-oauth-connect-design.md`.

**Key existing facts (do not re-derive):**
- `backend/app/services/email/oauth.py`: `_TOKEN_URL` (google/microsoft, MS has `{tenant}`), `_SAFE_TENANT` regex, `_SCOPE` (google `https://mail.google.com/`, microsoft `https://outlook.office365.com/SMTP.Send offline_access`), `_TIMEOUT=15.0`, `OAuthTokenError`, `fetch_access_token(...)`.
- `backend/app/services/smtp_settings.py`: `SmtpSettingsService(session)` with `get()`, `upsert(...)` (Fernet-encrypts secrets), `resolve_send_config(row)`. Model singleton `SmtpSettings` (cols incl. `oauth_provider`, `oauth_client_id`, `oauth_client_secret_enc`, `oauth_refresh_token_enc`, `oauth_tenant_id`, `auth_method`).
- `backend/app/api/smtp.py`: router `prefix="/api/admin/smtp"`; routes guarded by `Depends(require_org(Action.USER_MANAGE))`, mutations add `Depends(enforce_csrf)`; `AuditService(session).record(...)`; `_out(row)` masks secrets.
- `backend/app/core/config.py`: `class Settings(BaseSettings)` (line 11), `session_secret: str` (18), `cors_allow_origins` (37). `get_settings()` is the cached accessor.
- `backend/app/core/crypto.py`: `encrypt(str)->bytes` / `decrypt(bytes)->str`.
- Tests: `backend/tests/test_email_oauth.py` uses `@respx.mock` + `respx.post(url).mock(return_value=httpx.Response(200, json={...}))`. `backend/tests/test_smtp_api.py` has `_seed(db_engine)` (creates `sa@x.io` superadmin + `reg@x.io`) and `_login(api_client, email)`; `csrf_headers(api_client)` from conftest.
- Frontend: `frontend/src/admin/smtpHooks.ts` (`useSmtpSettings`/`useUpdateSmtpSettings`/`useTestSmtp`, `smtpKey()`); `frontend/src/pages/SmtpSettingsPage.tsx` (the OAuth branch at the `isOauth` ternary; `query.data.has_client_secret`/`has_refresh_token` flags; Mantine; `notifications.show`). i18n `smtp.*` block in `frontend/src/i18n/en.ts` (+ 12 siblings). Client regen: `npm run gen:api`.

**Backend test env** (run pytest as ONE compound command):
```
cd /home/l0rdg3x/coding/OPNGMS/backend && source .venv/bin/activate && \
export TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" \
ADMIN_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" \
DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" \
SESSION_SECRET="test-session-secret" \
MASTER_KEY="$(python -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')" && \
python -m pytest <args>
```
Run only the named tests (never the full suite concurrently). Frontend gate = `npm run build` (tsc -b + vite), `npm test`, `npm run lint`.

---

### Task 1: oauth.py — authorize URL, code exchange, signed state

**Files:**
- Modify: `backend/app/services/email/oauth.py`
- Test: `backend/tests/test_oauth_connect.py`

- [ ] **Step 1: failing tests** — `backend/tests/test_oauth_connect.py`:

```python
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from app.services.email.oauth import (
    OAuthTokenError,
    build_authorize_url,
    exchange_code,
    sign_state,
    verify_state,
)

_RID = "11111111-1111-1111-1111-111111111111"


def test_authorize_url_google():
    url = build_authorize_url("google", "cid.apps", "https://h/cb", "STATE", None)
    p = urlparse(url)
    q = parse_qs(p.query)
    assert p.scheme == "https" and p.netloc == "accounts.google.com"
    assert q["response_type"] == ["code"] and q["client_id"] == ["cid.apps"]
    assert q["redirect_uri"] == ["https://h/cb"] and q["state"] == ["STATE"]
    assert q["access_type"] == ["offline"] and q["prompt"] == ["consent"]
    assert q["scope"] == ["https://mail.google.com/"]


def test_authorize_url_microsoft_tenant_in_path():
    url = build_authorize_url("microsoft", "cid", "https://h/cb", "ST", "my-tenant")
    p = urlparse(url)
    assert p.netloc == "login.microsoftonline.com"
    assert p.path == "/my-tenant/oauth2/v2.0/authorize"
    q = parse_qs(p.query)
    assert q["scope"] == ["https://outlook.office365.com/SMTP.Send offline_access"]


def test_authorize_url_rejects_unknown_provider_and_unsafe_tenant():
    with pytest.raises(OAuthTokenError):
        build_authorize_url("nope", "c", "https://h/cb", "S", None)
    with pytest.raises(OAuthTokenError):
        build_authorize_url("microsoft", "c", "https://h/cb", "S", "../evil")


def test_state_roundtrip_and_rejections():
    s = sign_state(_RID, "google")
    assert verify_state(s, _RID, "google") is True
    assert verify_state(s, _RID, "microsoft") is False          # provider mismatch
    assert verify_state(s, "22222222-2222-2222-2222-222222222222", "google") is False  # user mismatch
    assert verify_state(s + "x", _RID, "google") is False        # tampered
    assert verify_state("not.a.state", _RID, "google") is False  # malformed
    # expired
    past = datetime.now(UTC) - timedelta(minutes=20)
    s_old = sign_state(_RID, "google", now=past)
    assert verify_state(s_old, _RID, "google") is False


@respx.mock
async def test_exchange_code_returns_refresh_and_access():
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(200, json={"refresh_token": "rt-123", "access_token": "at-123"}))
    out = await exchange_code("google", "cid", "secret", "auth-code", "https://h/cb", None)
    assert out == {"refresh_token": "rt-123", "access_token": "at-123"}


@respx.mock
async def test_exchange_code_raises_on_error_and_missing_refresh():
    respx.post("https://oauth2.googleapis.com/token").mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(OAuthTokenError):
        await exchange_code("google", "cid", "secret", "bad", "https://h/cb", None)
    respx.post("https://oauth2.googleapis.com/token").mock(  # 200 but no refresh_token
        return_value=httpx.Response(200, json={"access_token": "at-only"}))
    with pytest.raises(OAuthTokenError):
        await exchange_code("google", "cid", "secret", "code", "https://h/cb", None)
```

- [ ] **Step 2: run → fail** — `python -m pytest tests/test_oauth_connect.py -q` (ImportError).

- [ ] **Step 3: implement** — append to `backend/app/services/email/oauth.py` (after the existing code; add imports `hashlib`, `hmac`, `secrets`, `from datetime import UTC, datetime, timedelta`, `from urllib.parse import urlencode`, and `from app.core.config import get_settings`):

```python
_AUTHORIZE_URL = {
    "google": "https://accounts.google.com/o/oauth2/v2/auth",
    "microsoft": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
}
_STATE_TTL = timedelta(minutes=10)


def build_authorize_url(
    provider: str, client_id: str, redirect_uri: str, state: str, tenant_id: str | None
) -> str:
    """The user-facing consent URL. Fixed per-provider host; the only interpolated user value is the
    Microsoft tenant (sink-guarded), so this adds no SSRF / open-redirect surface."""
    if provider not in _AUTHORIZE_URL:
        raise OAuthTokenError(f"unsupported oauth provider: {provider}")
    tenant = tenant_id or "common"
    if not _SAFE_TENANT.match(tenant):
        raise OAuthTokenError("invalid tenant id")
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": _SCOPE[provider],
        "state": state,
    }
    if provider == "google":
        # access_type=offline + prompt=consent make Google return a refresh_token on every consent.
        params["access_type"] = "offline"
        params["prompt"] = "consent"
    else:
        params["response_mode"] = "query"
    base = _AUTHORIZE_URL[provider].format(tenant=tenant)
    return f"{base}?{urlencode(params)}"


async def exchange_code(
    provider: str, client_id: str, client_secret: str, code: str, redirect_uri: str,
    tenant_id: str | None,
) -> dict:
    """Exchange an authorization code for {refresh_token, access_token}. Raises OAuthTokenError on
    failure or a missing refresh token (no token material in the message)."""
    if provider not in _TOKEN_URL:
        raise OAuthTokenError(f"unsupported oauth provider: {provider}")
    tenant = tenant_id or "common"
    if not _SAFE_TENANT.match(tenant):
        raise OAuthTokenError("invalid tenant id")
    url = _TOKEN_URL[provider].format(tenant=tenant)
    data = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
        "scope": _SCOPE[provider],
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as http:
            resp = await http.post(url, data=data)
    except httpx.HTTPError as exc:
        raise OAuthTokenError("token endpoint unreachable") from exc
    if resp.status_code != 200:
        raise OAuthTokenError(f"code exchange failed ({resp.status_code})")
    body = resp.json()
    refresh = body.get("refresh_token")
    if not refresh:
        raise OAuthTokenError("no refresh_token in response")
    return {"refresh_token": refresh, "access_token": body.get("access_token")}


def _state_sig(payload: str) -> str:
    return hmac.new(get_settings().session_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def sign_state(user_id, provider: str, *, now: datetime | None = None) -> str:
    """A stateless, signed, expiring CSRF state bound to (user, provider)."""
    exp = int(((now or datetime.now(UTC)) + _STATE_TTL).timestamp())
    nonce = secrets.token_urlsafe(8)
    payload = f"{user_id}.{provider}.{exp}.{nonce}"
    return f"{payload}.{_state_sig(payload)}"


def verify_state(state: str, user_id, provider: str, *, now: datetime | None = None) -> bool:
    parts = state.rsplit(".", 1)
    if len(parts) != 2:
        return False
    payload, sig = parts
    if not hmac.compare_digest(sig, _state_sig(payload)):
        return False
    fields = payload.split(".")
    if len(fields) != 4:
        return False
    s_user, s_provider, s_exp, _nonce = fields
    if s_user != str(user_id) or s_provider != provider:
        return False
    try:
        exp = int(s_exp)
    except ValueError:
        return False
    return (now or datetime.now(UTC)).timestamp() < exp
```

- [ ] **Step 4: run → pass** — `python -m pytest tests/test_oauth_connect.py -q` (all pass); `ruff check app/services/email/oauth.py`.

- [ ] **Step 5: commit** `feat(smtp): oauth authorize-url builder + code exchange + signed state`.

---

### Task 2: config + service + the authorize/callback routes

**Files:**
- Modify: `backend/app/core/config.py` (add `public_base_url`)
- Modify: `backend/app/services/smtp_settings.py` (add `store_oauth_refresh_token`)
- Modify: `backend/app/api/smtp.py` (two routes)
- Test: `backend/tests/test_smtp_oauth_connect_api.py`

- [ ] **Step 1: failing test** — `backend/tests/test_smtp_oauth_connect_api.py`:

```python
from urllib.parse import parse_qs, urlparse

import httpx
import respx
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import crypto
from app.core.config import get_settings
from app.models.smtp_settings import SmtpSettings
from app.services.email import oauth as oauth_svc
from app.services.smtp_settings import SmtpSettingsService
from tests.conftest import csrf_headers
from tests.factories import make_user


async def _seed(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        sa = await make_user(s, email="sa@x.io", password="pw12345-secure", is_superadmin=True)
        await make_user(s, email="reg@x.io", password="pw12345-secure")
        await s.commit()
        return sa.id


async def _login(api_client, email="sa@x.io"):
    await api_client.post("/api/login", json={"email": email, "password": "pw12345-secure"})


async def _save_creds(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await SmtpSettingsService(s).upsert(
            enabled=True, host="smtp.x", port=587, security="starttls", username=None,
            from_email="f@x.io", from_name="F", password=None, clear_password=False,
            auth_method="oauth", oauth_provider="google", oauth_client_id="cid.apps",
            oauth_client_secret="secret", oauth_tenant_id=None)
        await s.commit()


async def test_authorize_requires_public_base_url(api_client, db_engine, monkeypatch):
    await _seed(db_engine)
    await _save_creds(db_engine)
    monkeypatch.setattr(get_settings(), "public_base_url", "", raising=False)
    await _login(api_client)
    r = await api_client.get("/api/admin/smtp/oauth/google/authorize")
    assert r.status_code == 409


async def test_authorize_unknown_provider_404(api_client, db_engine, monkeypatch):
    await _seed(db_engine)
    monkeypatch.setattr(get_settings(), "public_base_url", "https://opngms.test", raising=False)
    await _login(api_client)
    r = await api_client.get("/api/admin/smtp/oauth/nope/authorize")
    assert r.status_code == 404


async def test_authorize_returns_url_for_superadmin(api_client, db_engine, monkeypatch):
    await _seed(db_engine)
    await _save_creds(db_engine)
    monkeypatch.setattr(get_settings(), "public_base_url", "https://opngms.test", raising=False)
    await _login(api_client)
    r = await api_client.get("/api/admin/smtp/oauth/google/authorize")
    assert r.status_code == 200, r.text
    url = r.json()["authorize_url"]
    q = parse_qs(urlparse(url).query)
    assert q["client_id"] == ["cid.apps"]
    assert q["redirect_uri"] == ["https://opngms.test/api/admin/smtp/oauth/google/callback"]
    assert "state" in q


async def test_authorize_forbidden_for_non_superadmin(api_client, db_engine, monkeypatch):
    await _seed(db_engine)
    monkeypatch.setattr(get_settings(), "public_base_url", "https://opngms.test", raising=False)
    await _login(api_client, "reg@x.io")
    r = await api_client.get("/api/admin/smtp/oauth/google/authorize")
    assert r.status_code == 403


@respx.mock
async def test_callback_stores_refresh_and_redirects(api_client, db_engine, monkeypatch):
    uid = await _seed(db_engine)
    await _save_creds(db_engine)
    monkeypatch.setattr(get_settings(), "public_base_url", "https://opngms.test", raising=False)
    await _login(api_client)
    state = oauth_svc.sign_state(uid, "google")
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(200, json={"refresh_token": "rt-new", "access_token": "at"}))
    r = await api_client.get(
        f"/api/admin/smtp/oauth/google/callback?code=auth-code&state={state}",
        follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "https://opngms.test/admin/smtp?oauth=success"
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        row = (await s.execute(SmtpSettings.__table__.select())).first()
        stored = await SmtpSettingsService(s).get()
        assert stored.oauth_refresh_token_enc is not None
        assert crypto.decrypt(stored.oauth_refresh_token_enc) == "rt-new"


async def test_callback_bad_state_redirects_error_no_write(api_client, db_engine, monkeypatch):
    await _seed(db_engine)
    await _save_creds(db_engine)
    monkeypatch.setattr(get_settings(), "public_base_url", "https://opngms.test", raising=False)
    await _login(api_client)
    r = await api_client.get(
        "/api/admin/smtp/oauth/google/callback?code=c&state=forged.state", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "https://opngms.test/admin/smtp?oauth=error"
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        stored = await SmtpSettingsService(s).get()
        assert stored.oauth_refresh_token_enc is None  # nothing written
```

- [ ] **Step 2: run → fail** — `python -m pytest tests/test_smtp_oauth_connect_api.py -q` (404/AttributeError).

- [ ] **Step 3: config** — in `backend/app/core/config.py`, after `cors_allow_origins` (line 37) add:

```python
    public_base_url: str = ""  # external base URL (e.g. https://opngms.example.com) for OAuth redirect URIs
```

- [ ] **Step 4: service** — in `backend/app/services/smtp_settings.py`, add a method on `SmtpSettingsService`:

```python
    async def store_oauth_refresh_token(self, provider: str, refresh_token: str) -> SmtpSettings:
        """Persist a refresh token obtained via the OAuth Connect flow onto the existing singleton
        (client id+secret must already be saved). Sets auth_method=oauth + the provider."""
        row = await self.get()
        if row is None:
            row = SmtpSettings(id=SINGLETON_ID)
            self.session.add(row)
        row.auth_method = "oauth"
        row.oauth_provider = provider
        row.oauth_refresh_token_enc = crypto.encrypt(refresh_token)
        await self.session.flush()
        return row
```

- [ ] **Step 5: routes** — in `backend/app/api/smtp.py`, add imports and two routes. Add to the top imports:

```python
from fastapi.responses import RedirectResponse

from app.core.config import get_settings
from app.services.email.oauth import (
    OAuthTokenError,
    build_authorize_url,
    exchange_code,
    sign_state,
    verify_state,
)
```

Add the routes (after `test_smtp`):

```python
_PROVIDERS = {"google", "microsoft"}


@router.get("/oauth/{provider}/authorize")
async def oauth_authorize(
    provider: str,
    user: User = Depends(require_org(Action.USER_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """⚠️ EXPERIMENTAL/UNTESTED browser OAuth flow. Build the consent URL for the saved client id."""
    if provider not in _PROVIDERS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown provider")
    base = get_settings().public_base_url.rstrip("/")
    if not base:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="PUBLIC_BASE_URL not configured")
    row = await SmtpSettingsService(session).get()
    if row is None or not row.oauth_client_id or row.oauth_client_secret_enc is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="save the client id and secret first")
    redirect_uri = f"{base}/api/admin/smtp/oauth/{provider}/callback"
    state = sign_state(user.id, provider)
    try:
        url = build_authorize_url(provider, row.oauth_client_id, redirect_uri, state, row.oauth_tenant_id)
    except OAuthTokenError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"authorize_url": url}


@router.get("/oauth/{provider}/callback")
async def oauth_callback(
    provider: str,
    request: Request,
    user: User = Depends(require_org(Action.USER_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """⚠️ EXPERIMENTAL/UNTESTED. The provider's browser redirect lands here (superadmin session via the
    SameSite=Lax cookie). The signed `state` is the CSRF defence. Any failure redirects with ?oauth=error
    (never surfaces token material). Mutating-GET is the standard OAuth callback shape."""
    base = get_settings().public_base_url.rstrip("/")
    landing_ok = f"{base}/admin/smtp?oauth=success"
    landing_err = f"{base}/admin/smtp?oauth=error"
    if provider not in _PROVIDERS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown provider")
    if not base:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="PUBLIC_BASE_URL not configured")
    code = request.query_params.get("code")
    state = request.query_params.get("state") or ""
    if not code or not verify_state(state, user.id, provider):
        return RedirectResponse(landing_err, status_code=status.HTTP_302_FOUND)
    svc = SmtpSettingsService(session)
    row = await svc.get()
    if row is None or not row.oauth_client_id or row.oauth_client_secret_enc is None:
        return RedirectResponse(landing_err, status_code=status.HTTP_302_FOUND)
    redirect_uri = f"{base}/api/admin/smtp/oauth/{provider}/callback"
    try:
        result = await exchange_code(
            provider, row.oauth_client_id, crypto.decrypt(row.oauth_client_secret_enc),
            code, redirect_uri, row.oauth_tenant_id)
    except OAuthTokenError:
        return RedirectResponse(landing_err, status_code=status.HTTP_302_FOUND)
    await svc.store_oauth_refresh_token(provider, result["refresh_token"])
    await AuditService(session).record(
        actor_user_id=user.id, tenant_id=None, action="smtp.oauth.connected",
        target_type="smtp_settings", target_id="1", ip=None, details={"provider": provider},
    )
    await session.commit()
    return RedirectResponse(landing_ok, status_code=status.HTTP_302_FOUND)
```

Add `Request` to the `fastapi` import at the top of the file (`from fastapi import APIRouter, Depends, HTTPException, Request, status`).

- [ ] **Step 6: run → pass** — `python -m pytest tests/test_smtp_oauth_connect_api.py tests/test_oauth_connect.py tests/test_smtp_api.py tests/test_audit_coverage.py -q`; `ruff check app/`.

> Note: the callback is a `GET`, so the audit-coverage guard (POST/PUT/PATCH/DELETE only) does not require it to audit — but it audits `smtp.oauth.connected` inline anyway. The `GET …/authorize` is read-only (no audit).

- [ ] **Step 7: commit** `feat(smtp): oauth connect authorize + callback routes + public_base_url`.

---

### Task 3: frontend — Connect button (experimental) + i18n

**Files:**
- Modify: `frontend/src/api/schema.d.ts` (regen)
- Modify: `frontend/src/admin/smtpHooks.ts` (add `useSmtpOAuthConnect`)
- Modify: `frontend/src/pages/SmtpSettingsPage.tsx` (button + badge + return handling)
- Modify: `frontend/src/i18n/en.ts` + 12 siblings
- Test: `frontend/src/pages/__tests__/smtpOauthConnect.test.tsx` (or extend an existing SMTP page test)

- [ ] **Step 1: regen client** — with the backend importable: `cd /home/l0rdg3x/coding/OPNGMS/frontend && npm run gen:api` (set `SESSION_SECRET`/`MASTER_KEY`/`DATABASE_URL`/`ADMIN_DATABASE_URL` env as in the PR1 gen:api step). Confirm `/api/admin/smtp/oauth/{provider}/authorize` appears in `src/api/schema.d.ts`.

- [ ] **Step 2: i18n** — add to the `smtp` object in `frontend/src/i18n/en.ts`:

```typescript
oauthConnect: "Connect account",
oauthConnectGoogle: "Connect with Google",
oauthConnectMicrosoft: "Connect with Microsoft 365",
oauthExperimental: "Experimental — untested",
oauthExperimentalNote: "Browser sign-in for OAuth has not been verified against a live provider. It needs PUBLIC_BASE_URL set and the redirect URI registered in your OAuth app. Pasting a refresh token above remains the supported path.",
oauthConnectNeedsCreds: "Save the client ID and secret first, then connect.",
oauthConnected: "Account connected.",
oauthConnectFailed: "Could not connect the account.",
```
Mirror the whole set (translated, correct diacritics) in all 12 siblings (`it es fr de pt nl ru ar zh zhTW ja`).

- [ ] **Step 3: failing test** — `frontend/src/pages/__tests__/smtpOauthConnect.test.tsx`: render `SmtpSettingsPage` as a superadmin (reuse the existing SMTP test's auth/render helpers); mock `GET /api/admin/smtp` to return `auth_method: "oauth"`, `oauth_provider: "google"`, `has_client_secret: true`, `oauth_client_id: "cid"` (+ the other required fields). Assert the **Connect with Google** button (testid `smtp-oauth-connect`) renders with the experimental badge. Mock `GET /api/admin/smtp/oauth/google/authorize` → `{authorize_url: "https://accounts.google.com/o/oauth2/v2/auth?x=1"}`; stub `window.location` assignment (e.g. `vi.stubGlobal` or a spy on a small `redirectTo` helper) — assert clicking the button fetches the authorize URL. Second case: `has_client_secret: false` → the button is replaced by the `oauthConnectNeedsCreds` hint (no `smtp-oauth-connect` button).

- [ ] **Step 4: hook** — in `frontend/src/admin/smtpHooks.ts` add:

```typescript
export function useSmtpOAuthConnect() {
  return useMutation({
    mutationFn: async (provider: "google" | "microsoft"): Promise<string> => {
      const { data, error } = await api.GET("/api/admin/smtp/oauth/{provider}/authorize", {
        params: { path: { provider } },
      });
      if (error || !data) throw new Error("Failed to start OAuth");
      return data.authorize_url;
    },
  });
}
```

- [ ] **Step 5: UI** — in `frontend/src/pages/SmtpSettingsPage.tsx`, inside the `isOauth` branch (after the refresh-token field / tenant field), add the Connect block. Use a tiny indirection so tests can stub navigation:

```tsx
// near the top of the component:
const connect = useSmtpOAuthConnect();
// ...
{query.data?.has_client_secret && query.data?.oauth_client_id ? (
  <Stack gap={4}>
    <Group gap="xs">
      <Button
        variant="light"
        loading={connect.isPending}
        data-testid="smtp-oauth-connect"
        onClick={async () => {
          try {
            const url = await connect.mutateAsync(form.values.oauth_provider as "google" | "microsoft");
            window.location.href = url;
          } catch {
            notifications.show({ color: "red", message: t.smtp.oauthConnectFailed });
          }
        }}
      >
        {isMicrosoft ? t.smtp.oauthConnectMicrosoft : t.smtp.oauthConnectGoogle}
      </Button>
      <Badge color="yellow" variant="light">{t.smtp.oauthExperimental}</Badge>
    </Group>
    <Text size="xs" c="dimmed">{t.smtp.oauthExperimentalNote}</Text>
  </Stack>
) : (
  <Text size="xs" c="dimmed">{t.smtp.oauthConnectNeedsCreds}</Text>
)}
```

Add `Badge` to the `@mantine/core` import. Add a `useEffect` near the top that reads the OAuth return:

```tsx
useEffect(() => {
  const params = new URLSearchParams(window.location.search);
  const oauth = params.get("oauth");
  if (oauth === "success" || oauth === "error") {
    notifications.show(
      oauth === "success"
        ? { color: "green", message: t.smtp.oauthConnected }
        : { color: "red", message: t.smtp.oauthConnectFailed },
    );
    if (oauth === "success") query.refetch();
    params.delete("oauth");
    const qs = params.toString();
    window.history.replaceState({}, "", window.location.pathname + (qs ? `?${qs}` : ""));
  }
}, []);  // eslint-disable-line react-hooks/exhaustive-deps
```

- [ ] **Step 6: verify** — `npm test -- smtp`; `npm run build`; `npm run lint`.

- [ ] **Step 7: commit** `feat(smtp): experimental OAuth Connect button + return handling + i18n`.

---

### Task 4: docs + gate

**Files:** `CHANGELOG.md`, `README.md`, `.env.example` (if present), Wiki (handled by controller post-merge)

- [ ] **Step 1:** `CHANGELOG.md` — under `## [Unreleased]` add a `## [0.22.0] - 2026-06-17` block describing the **experimental** OAuth Connect button (note it's untested against a live provider; requires `PUBLIC_BASE_URL` + a registered redirect URI; manual refresh-token entry remains supported).
- [ ] **Step 2:** `README.md` — note the experimental Connect flow + `PUBLIC_BASE_URL` near the SMTP/OAuth mention; add `PUBLIC_BASE_URL` to `.env.example` (or the env table) if such a file exists (`grep -rl PUBLIC_BASE_URL .env.example docs 2>/dev/null` to find it; if none, skip).
- [ ] **Step 3: full gate** — backend: `python -m pytest tests/test_oauth_connect.py tests/test_smtp_oauth_connect_api.py tests/test_smtp_api.py tests/test_email_oauth.py tests/test_audit_coverage.py -q` + `ruff check app/`. Frontend: `cd frontend && npm run build && npm test && npm run lint`.
- [ ] **Step 4: commit** `docs: experimental SMTP OAuth Connect (CHANGELOG 0.22.0 + README + .env)`.

---

## Self-review notes
- Spec coverage: authorize-url/exchange/state (T1), config+service+routes (T2), button+i18n+return (T3), docs+gate (T4). All spec areas mapped.
- Type consistency: `build_authorize_url`/`exchange_code`/`sign_state`/`verify_state` signatures identical across T1↔T2; `store_oauth_refresh_token(provider, refresh_token)` defined T2, used T2; `useSmtpOAuthConnect(provider)` defined T3, used T3.
- Security: state HMAC+TTL+user+provider, callback re-checks user==session, redirect_uri/landing from server config (no open-redirect), token never logged/returned, superadmin-gated. Adversarial security review after T3.
- Untested labelling: experimental badge + note (T3), CHANGELOG/README marked experimental (T4) — the explicit user requirement.
