# SEC-3 — Session Lifecycle Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden sessions and CSRF: opaque hashed session tokens, idle + absolute expiry, logout-all, anti-fixation rotation, a real per-session CSRF token, an expired-session cleanup cron, and an active-sessions endpoint/UI.

**Architecture:** The session cookie carries an opaque random token; the DB stores only its SHA-256 (`token_hash`, unique). A per-session `csrf_token` is delivered via a readable `opngms_csrf` cookie and validated (constant-time) against the `X-OPNGMS-CSRF` header. A shared `get_current_session` dependency resolves+validates the session (absolute `expires_at` + idle `last_seen_at`, with a throttled last-seen write) and feeds both `get_current_user` and `enforce_csrf`. An hourly ARQ cron purges expired/idle rows. The frontend reads the CSRF cookie and exposes a sessions view with "log out everywhere".

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async + asyncpg, Alembic, pydantic-settings, ARQ, secrets/hashlib; React 19 + Mantine v9 + TanStack Query + openapi-fetch + i18n; pytest/respx (backend), Vitest/RTL/MSW (frontend).

**Test environment:** the DB-backed tests need these env vars (a local TimescaleDB `backend-db-1` is up):
```
export TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test"
export ADMIN_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test"
export DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test"
export REDIS_URL="redis://localhost:6379"
```
Run backend tests with the project venv: `cd backend && ./.venv/bin/python -m pytest …`. Baseline is **377 passed**.

**Branch:** `sec3-session-lifecycle` (already created). Frequent commits per task. Merge to `main` via PR at the end (main will be protected).

---

## File Structure

- `backend/app/models/session.py` — add `token_hash`, `csrf_token`, `last_seen_at`, `ip`, `user_agent`.
- `backend/migrations/versions/0014_session_hardening.py` — new migration (clears sessions, adds columns + unique index).
- `backend/app/core/config.py` — add `session_idle_minutes`.
- `backend/app/core/deps.py` — `CSRF_COOKIE`; `get_current_session`; rebuild `get_current_user`; upgrade `enforce_csrf`.
- `backend/app/services/auth.py` — token-centric session methods.
- `backend/app/api/auth.py` — login sets both cookies + ip/ua + rotation; logout clears both; `logout-all`; `GET /sessions`.
- `backend/app/schemas/auth.py` — `SessionInfo` response model.
- `backend/app/worker.py` — `cleanup_expired_sessions` cron.
- `backend/tests/test_sessions.py` (new), `test_csrf.py` (rewrite), `test_auth.py` (two cookies), `test_worker_cleanup.py` (new).
- `frontend/src/api/csrf.ts` (new); `frontend/src/api/client.ts`, `frontend/src/reports/reportHooks.ts`, `frontend/src/reports/settingsHooks.ts` (read CSRF cookie).
- `frontend/src/security/` (new: sessions view + hooks) + route/nav/i18n wiring.
- `README.md` — Roadmap & status row for SEC-3.

---

## Task 1: Session model fields + migration 0014

**Files:**
- Modify: `backend/app/models/session.py`
- Create: `backend/migrations/versions/0014_session_hardening.py`
- Test: `backend/tests/test_sessions.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_sessions.py`:

```python
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.session import Session


@pytest.fixture
async def factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def _make_user(factory) -> uuid.UUID:
    uid = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO users (id, email, name, password_hash, status, is_superadmin) "
                "VALUES (:id, :email, 'T', 'x', 'active', true)"
            ),
            {"id": uid, "email": f"{uid}@t.io"},
        )
        await s.commit()
    return uid


async def test_session_row_has_hardening_columns(factory):
    uid = await _make_user(factory)
    now = datetime.now(timezone.utc)
    async with factory() as s:
        sess = Session(
            user_id=uid,
            token_hash="a" * 64,
            csrf_token="c" * 43,
            last_seen_at=now,
            expires_at=now + timedelta(hours=12),
            ip="203.0.113.5",
            user_agent="pytest",
        )
        s.add(sess)
        await s.commit()
        row = (await s.execute(text("SELECT token_hash, csrf_token, ip, user_agent FROM sessions WHERE id=:i"), {"i": sess.id})).one()
        assert row.token_hash == "a" * 64
        assert row.ip == "203.0.113.5"
```

- [ ] **Step 2: Run it to verify it fails**

```
cd backend && ./.venv/bin/python -m pytest tests/test_sessions.py::test_session_row_has_hardening_columns -q
```
Expected: FAIL (`TypeError`/`unexpected keyword argument 'token_hash'` or an `UndefinedColumn` error).

- [ ] **Step 3: Add the model columns**

Replace `backend/app/models/session.py` with:

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class Session(UUIDPKMixin, Base):
    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # SHA-256 hex of the opaque bearer token. The raw token lives only in the cookie;
    # a DB dump therefore yields no usable session tokens.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # Per-session CSRF secret, echoed by the SPA in the X-OPNGMS-CSRF header.
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Updated (throttled) on activity to drive the idle/sliding timeout.
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # Display-only metadata for the active-sessions list.
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
```

- [ ] **Step 4: Create migration 0014**

Create `backend/migrations/versions/0014_session_hardening.py`:

```python
"""sessions: hardening columns (token_hash, csrf_token, last_seen_at, ip, user_agent)"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The bearer-token format changes from a raw UUID to a hashed opaque token, so
    # existing cookies can no longer be matched. Clearing the table lets the new
    # NOT NULL columns be added cleanly and forces a one-time re-login.
    op.execute("DELETE FROM sessions")
    op.add_column("sessions", sa.Column("token_hash", sa.String(64), nullable=False))
    op.add_column("sessions", sa.Column("csrf_token", sa.String(64), nullable=False))
    op.add_column(
        "sessions",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column("sessions", sa.Column("ip", sa.String(45), nullable=True))
    op.add_column("sessions", sa.Column("user_agent", sa.String(512), nullable=True))
    op.create_index("ix_sessions_token_hash", "sessions", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_sessions_token_hash", table_name="sessions")
    op.drop_column("sessions", "user_agent")
    op.drop_column("sessions", "ip")
    op.drop_column("sessions", "last_seen_at")
    op.drop_column("sessions", "csrf_token")
    op.drop_column("sessions", "token_hash")
```

- [ ] **Step 5: Run test to verify it passes**

```
cd backend && ./.venv/bin/python -m pytest tests/test_sessions.py::test_session_row_has_hardening_columns -q
```
Expected: PASS (the `db_engine` fixture rebuilds the schema from the model via `create_all`).

- [ ] **Step 6: Verify the migration applies on a real DB (offline + online)**

```
cd backend && ./.venv/bin/python -m alembic upgrade head && ./.venv/bin/python -m alembic downgrade -1 && ./.venv/bin/python -m alembic upgrade head
```
Expected: no error; `0014` is head. (Uses `ALEMBIC_DATABASE_URL`/`DATABASE_URL` from the env above.)

- [ ] **Step 7: Commit**

```
git add backend/app/models/session.py backend/migrations/versions/0014_session_hardening.py backend/tests/test_sessions.py
git commit -m "feat(sessions): add hardening columns + migration 0014"
```

---

## Task 2: Config — idle timeout setting + CSRF cookie name

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/core/deps.py` (add the cookie-name constant only)
- Test: `backend/tests/test_sessions.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_sessions.py`:

```python
def test_settings_have_idle_timeout():
    from app.core.config import Settings

    s = Settings(database_url="x", session_secret="x", master_key="x")
    assert s.session_idle_minutes == 120


def test_csrf_cookie_constant_exists():
    from app.core.deps import CSRF_COOKIE

    assert CSRF_COOKIE == "opngms_csrf"
```

- [ ] **Step 2: Run to verify it fails**

```
cd backend && ./.venv/bin/python -m pytest tests/test_sessions.py::test_settings_have_idle_timeout tests/test_sessions.py::test_csrf_cookie_constant_exists -q
```
Expected: FAIL (`AttributeError` / `ImportError`).

- [ ] **Step 3: Add the setting**

In `backend/app/core/config.py`, add after `session_ttl_hours`:

```python
    session_idle_minutes: int = 120  # sliding/idle timeout, alongside the absolute session_ttl_hours
```

- [ ] **Step 4: Add the cookie-name constant**

In `backend/app/core/deps.py`, below `SESSION_COOKIE = "opngms_session"` add:

```python
CSRF_COOKIE = "opngms_csrf"  # readable (non-httponly) cookie carrying the per-session CSRF token
```

- [ ] **Step 5: Run to verify it passes** — same command as Step 2. Expected: PASS.

- [ ] **Step 6: Commit**

```
git add backend/app/core/config.py backend/app/core/deps.py backend/tests/test_sessions.py
git commit -m "feat(sessions): add session_idle_minutes setting + CSRF cookie name"
```

---

## Task 3: AuthService — token-centric session methods

**Files:**
- Modify: `backend/app/services/auth.py`
- Test: `backend/tests/test_sessions.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_sessions.py`:

```python
from app.models.user import User
from app.services.auth import AuthService, _hash_token


async def _user_obj(factory) -> User:
    uid = await _make_user(factory)
    async with factory() as s:
        return await s.get(User, uid)


async def test_create_session_hashes_token(factory):
    async with factory() as s:
        user = await s.get(User, await _make_user(factory))
        sess, raw = await AuthService(s).create_session(user, ttl_hours=12, ip="203.0.113.9", user_agent="UA")
        await s.commit()
        assert raw and sess.token_hash == _hash_token(raw)
        assert sess.token_hash != raw  # stored value is the hash, not the token
        assert sess.csrf_token and sess.ip == "203.0.113.9"


async def test_get_session_for_token_roundtrip_and_expiry(factory):
    async with factory() as s:
        user = await s.get(User, await _make_user(factory))
        svc = AuthService(s)
        sess, raw = await svc.create_session(user, ttl_hours=12)
        await s.commit()
        got = await svc.get_session_for_token(raw)
        assert got is not None and got.id == sess.id
        assert await svc.get_session_for_token("not-a-real-token") is None


async def test_idle_timeout_rejects_stale_session(factory):
    from datetime import datetime, timedelta, timezone
    async with factory() as s:
        user = await s.get(User, await _make_user(factory))
        svc = AuthService(s)
        sess, raw = await svc.create_session(user, ttl_hours=12)
        sess.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=121)  # idle default 120
        await s.commit()
        assert await svc.get_session_for_token(raw) is None


async def test_logout_all_and_purge(factory):
    from datetime import datetime, timedelta, timezone
    async with factory() as s:
        user = await s.get(User, await _make_user(factory))
        svc = AuthService(s)
        a, ra = await svc.create_session(user, ttl_hours=12)
        b, rb = await svc.create_session(user, ttl_hours=12)
        await s.commit()
        assert len(await svc.list_sessions_for_user(user.id)) == 2
        await svc.delete_all_sessions_for_user(user.id)
        await s.commit()
        assert await svc.list_sessions_for_user(user.id) == []
        # purge: insert one already-expired session, confirm it is removed
        c, rc = await svc.create_session(user, ttl_hours=12)
        c.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await s.commit()
        n = await svc.purge_expired(datetime.now(timezone.utc))
        await s.commit()
        assert n == 1 and await svc.list_sessions_for_user(user.id) == []
```

- [ ] **Step 2: Run to verify they fail**

```
cd backend && ./.venv/bin/python -m pytest tests/test_sessions.py -k "hashes or roundtrip or idle or logout_all" -q
```
Expected: FAIL (`ImportError: _hash_token` / `create_session` signature / missing methods).

- [ ] **Step 3: Rewrite the service**

Replace `backend/app/services/auth.py` with:

```python
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import verify_password
from app.models.session import Session
from app.models.user import User

# Persist last_seen at most once per minute per session to bound write amplification
# while still enforcing the idle timeout at ~60s granularity.
_LAST_SEEN_THROTTLE = timedelta(seconds=60)


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def authenticate(self, email: str, password: str) -> User | None:
        result = await self.session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user is None or user.status != "active":
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user

    async def create_session(
        self,
        user: User,
        *,
        ttl_hours: int,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[Session, str]:
        """Create a session. Returns (session, raw_token). Only the hash is stored."""
        now = datetime.now(timezone.utc)
        raw_token = secrets.token_urlsafe(32)
        sess = Session(
            user_id=user.id,
            token_hash=_hash_token(raw_token),
            csrf_token=secrets.token_urlsafe(32),
            last_seen_at=now,
            expires_at=now + timedelta(hours=ttl_hours),
            ip=ip,
            user_agent=(user_agent[:512] if user_agent else None),
        )
        self.session.add(sess)
        user.last_login = now
        await self.session.flush()
        return sess, raw_token

    async def get_session_for_token(self, raw_token: str) -> Session | None:
        """Resolve+validate a session from its raw token (absolute + idle expiry)."""
        now = datetime.now(timezone.utc)
        idle = timedelta(minutes=get_settings().session_idle_minutes)
        result = await self.session.execute(
            select(Session).where(Session.token_hash == _hash_token(raw_token))
        )
        sess = result.scalar_one_or_none()
        if sess is None:
            return None
        if sess.expires_at <= now or (now - sess.last_seen_at) > idle:
            return None
        if (now - sess.last_seen_at) >= _LAST_SEEN_THROTTLE:
            sess.last_seen_at = now
            await self.session.commit()  # get_session() does not auto-commit; persist the touch
        return sess

    async def get_user_for_session(self, sess: Session) -> User | None:
        return await self.session.get(User, sess.user_id)

    async def delete_session_by_token(self, raw_token: str) -> None:
        await self.session.execute(
            delete(Session).where(Session.token_hash == _hash_token(raw_token))
        )

    async def delete_all_sessions_for_user(self, user_id: uuid.UUID) -> None:
        await self.session.execute(delete(Session).where(Session.user_id == user_id))

    async def list_sessions_for_user(self, user_id: uuid.UUID) -> list[Session]:
        result = await self.session.execute(
            select(Session)
            .where(Session.user_id == user_id)
            .order_by(Session.last_seen_at.desc())
        )
        return list(result.scalars().all())

    async def purge_expired(self, now: datetime) -> int:
        idle = timedelta(minutes=get_settings().session_idle_minutes)
        result = await self.session.execute(
            delete(Session).where(
                (Session.expires_at <= now) | (Session.last_seen_at < now - idle)
            )
        )
        return result.rowcount or 0
```

- [ ] **Step 4: Run to verify they pass** — same command as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```
git add backend/app/services/auth.py backend/tests/test_sessions.py
git commit -m "feat(sessions): token-hashed, idle-aware AuthService session methods"
```

---

## Task 4: Dependencies — get_current_session, get_current_user, enforce_csrf

**Files:**
- Modify: `backend/app/core/deps.py`
- Test: `backend/tests/test_sessions.py`

> Context: `get_current_user` previously parsed a UUID and called `get_user_for_session(uuid)`. It now sits on top of `get_current_session`. `enforce_csrf` changes from header-presence to a constant-time compare against the session's `csrf_token` and therefore now depends on a valid session. `/login` and `/setup` must remain CSRF-exempt — they declare no `enforce_csrf` dependency; do not add one.

- [ ] **Step 1: Write the failing test (CSRF value validation)**

Replace `backend/tests/test_csrf.py` entirely:

```python
async def _login(api_client):
    await api_client.post(
        "/api/setup", json={"email": "a@a.io", "name": "A", "password": "pw-123456"}
    )
    await api_client.post("/api/login", json={"email": "a@a.io", "password": "pw-123456"})


def _csrf(api_client) -> str:
    # The login response set a readable opngms_csrf cookie; httpx stores it in the jar.
    return api_client.cookies.get("opngms_csrf")


async def test_logout_without_header_is_forbidden(api_client):
    await _login(api_client)
    r = await api_client.post("/api/logout")
    assert r.status_code == 403


async def test_logout_with_wrong_token_is_forbidden(api_client):
    await _login(api_client)
    r = await api_client.post("/api/logout", headers={"X-OPNGMS-CSRF": "wrong"})
    assert r.status_code == 403


async def test_logout_with_session_token_succeeds(api_client):
    await _login(api_client)
    r = await api_client.post("/api/logout", headers={"X-OPNGMS-CSRF": _csrf(api_client)})
    assert r.status_code == 204
```

- [ ] **Step 2: Run to verify it fails**

```
cd backend && ./.venv/bin/python -m pytest tests/test_csrf.py -q
```
Expected: FAIL (currently any header value passes, and no `opngms_csrf` cookie is set yet — so `test_logout_with_wrong_token_is_forbidden` fails). Note: Tasks 4 and 5 are interdependent (deps + login cookie); commit them together if a test needs both. It is fine for some test_csrf cases to stay red until Task 5 is done — run the combined suite at the end of Task 5.

- [ ] **Step 3: Rewrite the dependencies**

In `backend/app/core/deps.py`: add `import secrets` at the top, import the model (`from app.models.session import Session`), and replace `enforce_csrf` + `get_current_user` with:

```python
async def get_current_session(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Session:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    sess = await AuthService(session).get_session_for_token(raw)
    if sess is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    request.state.session = sess  # used by enforce_csrf and the current-session flag in GET /sessions
    return sess


async def enforce_csrf(
    request: Request, sess: Session = Depends(get_current_session)
) -> None:
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        header = request.headers.get(CSRF_HEADER)
        if not header or not secrets.compare_digest(header, sess.csrf_token):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="CSRF check failed"
            )


async def get_current_user(
    sess: Session = Depends(get_current_session),
    session: AsyncSession = Depends(get_session),
) -> User:
    user = await AuthService(session).get_user_for_session(sess)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    return user
```

Remove the now-unused `import uuid` if it is no longer referenced in the file (check `tenant_context`/others first — `uuid.UUID` is still used there, so keep it).

- [ ] **Step 4: Run the dependency-level checks**

```
cd backend && ./.venv/bin/python -m pytest tests/test_auth.py -q
```
Expected: `test_auth.py` login/me still pass (logout test may need Task 5's cookie — see note). Proceed to Task 5; the full re-run happens there.

- [ ] **Step 5: Commit**

```
git add backend/app/core/deps.py backend/tests/test_csrf.py
git commit -m "feat(sessions): session-backed deps + value-checked CSRF"
```

---

## Task 5: API — login cookies + rotation, logout, logout-all, GET /sessions

**Files:**
- Modify: `backend/app/api/auth.py`
- Modify: `backend/app/schemas/auth.py` (add `SessionInfo`)
- Test: `backend/tests/test_sessions.py`, `backend/tests/test_auth.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_sessions.py`:

```python
async def _setup_login(api_client):
    await api_client.post("/api/setup", json={"email": "a@a.io", "name": "A", "password": "pw-123456"})
    await api_client.post("/api/login", json={"email": "a@a.io", "password": "pw-123456"})


async def test_login_sets_both_cookies(api_client):
    await api_client.post("/api/setup", json={"email": "a@a.io", "name": "A", "password": "pw-123456"})
    r = await api_client.post("/api/login", json={"email": "a@a.io", "password": "pw-123456"})
    assert r.status_code == 200
    assert api_client.cookies.get("opngms_session")
    assert api_client.cookies.get("opngms_csrf")


async def test_get_sessions_lists_current(api_client):
    await _setup_login(api_client)
    r = await api_client.get("/api/sessions")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1 and rows[0]["current"] is True
    assert "ip" in rows[0] and "user_agent" in rows[0]


async def test_logout_all_kills_every_session(api_client):
    await _setup_login(api_client)
    csrf = api_client.cookies.get("opngms_csrf")
    r = await api_client.post("/api/logout-all", headers={"X-OPNGMS-CSRF": csrf})
    assert r.status_code == 204
    assert (await api_client.get("/api/me")).status_code == 401
```

- [ ] **Step 2: Run to verify they fail**

```
cd backend && ./.venv/bin/python -m pytest tests/test_sessions.py -k "both_cookies or lists_current or logout_all_kills" -q
```
Expected: FAIL (no `opngms_csrf` cookie / no `/sessions` / no `/logout-all`).

- [ ] **Step 3: Add the `SessionInfo` schema**

In `backend/app/schemas/auth.py`, add:

```python
import uuid
from datetime import datetime

from pydantic import BaseModel


class SessionInfo(BaseModel):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    ip: str | None
    user_agent: str | None
    current: bool
```

(Keep the existing `LoginIn`/`MeOut`; only add `SessionInfo` and any imports it needs.)

- [ ] **Step 4: Update the API**

In `backend/app/api/auth.py`:

1. Update imports:
```python
from app.core.deps import CSRF_COOKIE, SESSION_COOKIE, enforce_csrf, get_current_session, get_current_user
from app.models.session import Session
from app.schemas.auth import LoginIn, MeOut, SessionInfo
```
2. Add a small cookie helper near the top (after `router = …`):
```python
def _client_ip(request: Request) -> str | None:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else None
```
3. In `login`, replace the session-creation + cookie block (current lines ~75–98) with:
```python
    settings = get_settings()
    # Anti-fixation rotation: drop any session presented in the incoming cookie.
    old = request.cookies.get(SESSION_COOKIE)
    if old:
        await svc.delete_session_by_token(old)
    sess, raw_token = await svc.create_session(
        user,
        ttl_hours=settings.session_ttl_hours,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    try:
        login_limiter.reset(key)
    except Exception:  # noqa: BLE001 — never let a limiter fault break a successful login
        logger.error("login rate-limiter reset failed", exc_info=True)
    await AuditService(session).record(
        actor_user_id=user.id, tenant_id=None, action="auth.login",
        target_type="session", target_id=str(sess.id), ip=_client_ip(request), details={},
    )
    await session.commit()
    max_age = settings.session_ttl_hours * 3600
    response.set_cookie(SESSION_COOKIE, raw_token, httponly=True, secure=True, samesite="lax", max_age=max_age)
    response.set_cookie(CSRF_COOKIE, sess.csrf_token, httponly=False, secure=True, samesite="lax", max_age=max_age)
    return user
```
4. Replace `logout` body to delete by token and clear both cookies:
```python
@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(enforce_csrf)])
async def logout(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        await AuthService(session).delete_session_by_token(raw)
        await AuditService(session).record(
            actor_user_id=user.id, tenant_id=None, action="auth.logout",
            target_type="session", target_id=None,
            ip=request.client.host if request.client else None, details={},
        )
        await session.commit()
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(CSRF_COOKIE)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
```
5. Add the two new endpoints (after `logout`, before `me`):
```python
@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(enforce_csrf)])
async def logout_all(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await AuthService(session).delete_all_sessions_for_user(user.id)
    await AuditService(session).record(
        actor_user_id=user.id, tenant_id=None, action="auth.logout_all",
        target_type="user", target_id=str(user.id),
        ip=request.client.host if request.client else None, details={},
    )
    await session.commit()
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(CSRF_COOKIE)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/sessions", response_model=list[SessionInfo])
async def list_sessions(
    user: User = Depends(get_current_user),
    current: Session = Depends(get_current_session),
    session: AsyncSession = Depends(get_session),
) -> list[SessionInfo]:
    rows = await AuthService(session).list_sessions_for_user(user.id)
    return [
        SessionInfo(
            id=r.id, created_at=r.created_at, last_seen_at=r.last_seen_at,
            expires_at=r.expires_at, ip=r.ip, user_agent=r.user_agent,
            current=(r.id == current.id),
        )
        for r in rows
    ]
```

- [ ] **Step 5: Run the session + csrf + auth suites**

```
cd backend && ./.venv/bin/python -m pytest tests/test_sessions.py tests/test_csrf.py tests/test_auth.py -q
```
Expected: PASS (all of Task 4's CSRF cases now pass too).

- [ ] **Step 6: Commit**

```
git add backend/app/api/auth.py backend/app/schemas/auth.py backend/tests/test_sessions.py backend/tests/test_auth.py
git commit -m "feat(sessions): login dual-cookie + rotation, logout-all, GET /sessions"
```

---

## Task 6: Worker — expired-session cleanup cron

**Files:**
- Modify: `backend/app/worker.py`
- Test: `backend/tests/test_worker_cleanup.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_worker_cleanup.py`:

```python
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.auth import AuthService
from app.worker import cleanup_expired_sessions


@pytest.fixture
async def factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def test_cleanup_cron_purges_expired(factory):
    uid = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO users (id, email, name, password_hash, status, is_superadmin) "
                "VALUES (:id, :e, 'T', 'x', 'active', true)"
            ),
            {"id": uid, "e": f"{uid}@t.io"},
        )
        await s.commit()
        user = await s.get(__import__("app.models.user", fromlist=["User"]).User, uid)
        svc = AuthService(s)
        live, _ = await svc.create_session(user, ttl_hours=12)
        dead, _ = await svc.create_session(user, ttl_hours=12)
        dead.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await s.commit()

    result = await cleanup_expired_sessions({"session_factory": factory})
    assert "purged 1" in result
    async with factory() as s:
        remaining = (await s.execute(text("SELECT count(*) FROM sessions"))).scalar_one()
        assert remaining == 1
```

- [ ] **Step 2: Run to verify it fails**

```
cd backend && ./.venv/bin/python -m pytest tests/test_worker_cleanup.py -q
```
Expected: FAIL (`ImportError: cannot import name 'cleanup_expired_sessions'`).

- [ ] **Step 3: Add the cron function + registration**

In `backend/app/worker.py`:
1. Add the function (near the other `enqueue_*` functions):
```python
async def cleanup_expired_sessions(ctx: dict) -> str:
    """Cron: delete expired/idle sessions. Returns a short status string."""
    factory = ctx["session_factory"]
    async with factory() as session:
        from app.services.auth import AuthService  # local import avoids a cycle at module load

        n = await AuthService(session).purge_expired(datetime.now(timezone.utc))
        await session.commit()
    return f"purged {n} expired sessions"
```
2. Register it in `WorkerSettings`:
```python
    functions = [poll_device, ingest_device_events, backup_device_config, apply_config_change, generate_tenant_report]
    cron_jobs = [
        cron(enqueue_device_polls, second={0}),
        cron(enqueue_event_ingests, minute=set(range(0, 60, 5))),
        cron(enqueue_config_backups, hour={3}, minute={0}),
        cron(enqueue_scheduled_reports, weekday="mon", hour={4}, minute={0}),
        cron(cleanup_expired_sessions, minute={0}),  # hourly: reap expired/idle sessions
    ]
```
(If `AuthService` is already imported at module top elsewhere, use that instead of the local import.)

- [ ] **Step 4: Run to verify it passes** — same command as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```
git add backend/app/worker.py backend/tests/test_worker_cleanup.py
git commit -m "feat(sessions): hourly expired-session cleanup cron"
```

---

## Task 7: Full backend suite gate

**Files:** none (verification only)

- [ ] **Step 1: Run the whole backend suite**

```
cd backend && ./.venv/bin/python -m pytest -q
```
Expected: all green (baseline 377 + the new session/cleanup tests). Investigate and fix any regression (e.g. other tests that assumed the old single-cookie login or header-presence CSRF) before continuing.

- [ ] **Step 2: Commit any fixups**

```
git add -A && git commit -m "test(sessions): fix fallout from session/CSRF hardening"
```

---

## Task 8: Frontend — send the real CSRF token

**Files:**
- Create: `frontend/src/api/csrf.ts`
- Modify: `frontend/src/api/client.ts`, `frontend/src/reports/reportHooks.ts`, `frontend/src/reports/settingsHooks.ts`
- Test: `frontend/src/api/csrf.test.ts` (new)

- [ ] **Step 1: Write the failing test**

Create `frontend/src/api/csrf.test.ts`:

```ts
import { describe, expect, it, beforeEach } from "vitest";
import { csrfToken } from "./csrf";

describe("csrfToken", () => {
  beforeEach(() => {
    document.cookie = "opngms_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  });
  it("reads the opngms_csrf cookie", () => {
    document.cookie = "opngms_csrf=abc123";
    expect(csrfToken()).toBe("abc123");
  });
  it("returns empty string when absent", () => {
    expect(csrfToken()).toBe("");
  });
});
```

- [ ] **Step 2: Run to verify it fails**

```
cd frontend && npx vitest run src/api/csrf.test.ts
```
Expected: FAIL (module `./csrf` not found).

- [ ] **Step 3: Implement the util**

Create `frontend/src/api/csrf.ts`:

```ts
// Reads the per-session CSRF token from the readable `opngms_csrf` cookie set at login.
// The value is echoed back in the X-OPNGMS-CSRF header on mutating requests.
export function csrfToken(): string {
  const m = document.cookie.match(/(?:^|;\s*)opngms_csrf=([^;]*)/);
  return m ? decodeURIComponent(m[1]) : "";
}
```

- [ ] **Step 4: Use it in the three call sites**

In `frontend/src/api/client.ts`, replace the hardcoded value:
```ts
import { csrfToken } from "./csrf";
// …
      request.headers.set("X-OPNGMS-CSRF", csrfToken());
```
In `frontend/src/reports/reportHooks.ts` and `frontend/src/reports/settingsHooks.ts`, replace `"X-OPNGMS-CSRF": "1"` with `"X-OPNGMS-CSRF": csrfToken()` (add `import { csrfToken } from "../api/csrf";`).

- [ ] **Step 5: Run to verify it passes + fix any MSW tests**

```
cd frontend && npx vitest run src/api/csrf.test.ts && npx vitest run
```
Expected: PASS. If any existing test asserted the literal `"1"` header, update it to accept the cookie-derived token (or set the cookie in the test).

- [ ] **Step 6: Commit**

```
git add frontend/src/api/csrf.ts frontend/src/api/client.ts frontend/src/reports/reportHooks.ts frontend/src/reports/settingsHooks.ts frontend/src/api/csrf.test.ts
git commit -m "feat(frontend): send per-session CSRF token from cookie"
```

---

## Task 9: Frontend — active-sessions view + "log out everywhere"

**Files:**
- Create: `frontend/src/security/SessionsPage.tsx`, `frontend/src/security/sessionHooks.ts`
- Modify: routing + nav (follow the existing pattern, e.g. `frontend/src/App.tsx` / the router/nav module) + i18n locale files
- Test: `frontend/src/security/SessionsPage.test.tsx`

> Context: follow the existing page/route/nav/i18n conventions already used by other views (e.g. the reports/settings pages). The implementer should inspect a sibling page (such as the reports settings page) and mirror its structure, query hooks, Mantine components, and `useTranslation` usage.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/security/SessionsPage.test.tsx` rendering `SessionsPage` with MSW returning two sessions (one `current: true`) and a mocked `POST /api/logout-all`; assert both rows render, the current one is badged, and clicking "Log out everywhere" calls the endpoint. (Mirror the MSW + render-helper setup used by an existing `*.test.tsx`.)

- [ ] **Step 2: Run to verify it fails**

```
cd frontend && npx vitest run src/security/SessionsPage.test.tsx
```
Expected: FAIL (component does not exist).

- [ ] **Step 3: Implement hooks**

Create `frontend/src/security/sessionHooks.ts`: a `useSessions()` TanStack Query hook calling `GET /api/sessions` via the typed `api` client, and a `useLogoutAll()` mutation calling `POST /api/logout-all` (the CSRF header is added automatically by the client middleware), invalidating the sessions query and redirecting to login on success. Mirror `frontend/src/reports/settingsHooks.ts`.

- [ ] **Step 4: Implement the page**

Create `frontend/src/security/SessionsPage.tsx`: a Mantine table listing sessions (last seen, created, expires, IP, user agent), the current session badged, and a "Log out everywhere" button wired to `useLogoutAll()`. Use `useTranslation` for all strings.

- [ ] **Step 5: Wire route + nav + i18n**

Add a route (e.g. `/security/sessions`) and a nav entry following the existing pattern; add the new i18n keys to each locale file already present (mirror how the reports page keys are declared). Keep keys English; do not hardcode visible strings.

- [ ] **Step 6: Run to verify it passes + full frontend suite**

```
cd frontend && npx vitest run && npm run build
```
Expected: PASS and a clean production build.

- [ ] **Step 7: Commit**

```
git add frontend/src/security frontend/src/<router/nav files> frontend/src/i18n/<locale files>
git commit -m "feat(frontend): active sessions view + log out everywhere"
```

---

## Task 10: Docs — README roadmap row

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add the SEC-3 row**

In the `## Roadmap & status` table (and the SEC-* prose section if present), add a row documenting SEC-3: hashed session tokens, idle + absolute expiry, per-session CSRF token, logout-all, session rotation, hourly cleanup cron, active-sessions view. Mark ✅ Done. Keep wording consistent with the SEC-1/SEC-2 entries.

- [ ] **Step 2: Commit**

```
git add README.md
git commit -m "docs: README roadmap — SEC-3 session lifecycle hardening"
```

---

## Final verification (controller, after all tasks)

- [ ] Backend full suite green: `cd backend && ./.venv/bin/python -m pytest -q`
- [ ] Alembic round-trips: `upgrade head` → `downgrade -1` → `upgrade head` clean.
- [ ] Frontend: `cd frontend && npx vitest run && npm run build && npm run lint` green.
- [ ] Dispatch a final holistic (opus) review over the whole branch diff.
- [ ] Open a PR to `main` (main is protected); merge once CI / Container Image Scan / gitleaks are green.
