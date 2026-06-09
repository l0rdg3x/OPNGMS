# OPNGMS Phase 1 · Milestone B — Auth, Sessions, RBAC & Org-Admin — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give OPNGMS session-based authentication, 4-role RBAC access control with audit log, the request-context that wires `app.current_tenant` (finally activating RLS on every request), and the org-admin CRUD for tenants/users/memberships — making the platform a fully protected vertical.

**Architecture:** Layered async FastAPI. **Server-side session** auth (`sessions` table, `httpOnly`/`secure`/`SameSite=Lax` cookie) with **argon2** password hashing. CSRF mitigated by `SameSite=Lax` + a mandatory custom header on mutations. A **request-context** resolves the user from the session and, for routes under `/api/tenants/{tenant_id}/...`, authorises access (superadmin or membership) and sets `app.current_tenant` (making Milestone A's RLS effective at runtime). An RBAC **policy layer** evaluates `(role x action)` against an explicit matrix. The first superadmin is created via a **one-time setup endpoint** disabled as soon as a user exists.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy 2.0 async + asyncpg, Alembic, argon2-cffi, pydantic v2, Postgres, pytest + httpx.

---

## Spec reference

Implements sections **8 (AuthN), 9 (RBAC + matrix), 10 (Audit)** of the spec
`docs/superpowers/specs/2026-06-08-opngms-foundation-inventory-design.md`, plus the
request-context from sec. 7 (wiring `app.current_tenant`). Additional decisions made during
planning: org-admin CRUD included here; first superadmin via one-time setup endpoint;
CSRF via `SameSite=Lax` + custom header.

## Prerequisites (from Milestone A, already in `main`)
- Models `User` (email, name, password_hash, is_superadmin, status, last_login), `Tenant`,
  `Membership` (user_id, tenant_id, role), `Session` (id, user_id, created_at, expires_at),
  `AuditLog`, `Device`.
- `app/core/db.py` with `get_session`, `set_tenant_context(session, tenant_id)`.
- The app connects as the non-superuser role `opngms_app`; control-plane tables
  (users/tenants/memberships/sessions/audit_log) are NOT under RLS, so they are readable/
  writable by the app and must be filtered at the service layer. Only `devices` has RLS.

## File structure (created/modified in this milestone)

```
backend/
  app/
    core/
      security.py        # hash/verify password (argon2)
      rbac.py            # roles, actions, permission matrix, can()
      deps.py            # dependency: get_current_user, csrf, tenant-context, require_*
      cli.py             # (no) -> not used (setup via endpoint)
    models/
      base.py            # + updated_at on TimestampMixin
    schemas/             # NEW: pydantic I/O models
      __init__.py
      auth.py            # SetupIn, LoginIn, UserOut, MeOut
      tenant.py          # TenantIn, TenantOut
      user.py            # UserCreateIn, UserOut
      membership.py      # MembershipIn, MembershipOut
    repositories/
      user.py            # NEW
      tenant.py          # NEW
      membership.py      # NEW
    services/
      auth.py            # login/logout/session lifecycle
      audit.py           # AuditService.record(...)
    api/                 # NEW: routers
      __init__.py
      setup.py           # POST /api/setup (one-time)
      auth.py            # POST /api/login, POST /api/logout, GET /api/me
      tenants.py         # /api/tenants ... (superadmin)
      users.py           # /api/users ... (superadmin)
      memberships.py     # /api/tenants/{tenant_id}/memberships ... (superadmin|tenant_admin)
    main.py              # include routers, session/CSRF wiring
  migrations/versions/
    0004_indexes_updated_at.py   # indexes on sessions/memberships/audit + updated_at
  tests/
    test_security.py
    test_setup_endpoint.py
    test_auth.py
    test_csrf.py
    test_rbac_matrix.py
    test_tenant_context.py
    test_audit.py
    test_tenants_api.py
    test_users_api.py
    test_memberships_api.py
    test_b_integration.py
    factories.py         # helper to create user/tenant/membership in tests
```

---

## Task 1: Migration 0004 — indexes + `updated_at`

Closes part of the Milestone A technical debt now that queries and mutations arrive on these tables.

**Files:**
- Modify: `backend/app/models/base.py`
- Create: `backend/migrations/versions/0004_indexes_updated_at.py`
- Test: `backend/tests/test_migration_0004.py`

- [ ] **Step 1: Write the failing test**

`backend/tests/test_migration_0004.py`:
```python
from app.models.tenant import Tenant


def test_timestamp_mixin_has_updated_at():
    cols = {c.name for c in Tenant.__table__.columns}
    assert "updated_at" in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_migration_0004.py -v`
Expected: FAIL — `updated_at` not in columns.

- [ ] **Step 3: Add `updated_at` to the mixin AND declare the indexes on the models**

IMPORTANT: the indexes must be declared on the models (not only in the migration), otherwise
`alembic check` (Step 5) detects drift and wants to drop them. Model metadata and migration must
match.

In `backend/app/models/base.py`, extend `TimestampMixin`:
```python
class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```
(`func` and `datetime`/`DateTime` are already imported in this file.)

In `backend/app/models/session.py`, add `index=True` to both columns:
```python
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    ...
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
```

In `backend/app/models/membership.py`, add `index=True` to `tenant_id`:
```python
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
```

In `backend/app/models/audit.py`, add `index=True` to `tenant_id`, `actor_user_id`, `ts`:
```python
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), default=None, index=True
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), default=None, index=True
    )
```
Autogenerate names these `ix_<table>_<column>`, matching the index names the migration creates,
so `alembic check` stays clean.

- [ ] **Step 4: Write the migration**

`backend/migrations/versions/0004_indexes_updated_at.py`:
```python
"""indexes (sessions/memberships/audit) + updated_at on TimestampMixin tables"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

# Tables that use TimestampMixin (have created_at and now updated_at).
_TIMESTAMP_TABLES = ["tenants", "users", "memberships", "devices"]


def upgrade() -> None:
    for table in _TIMESTAMP_TABLES:
        op.add_column(
            table,
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])
    op.create_index("ix_memberships_tenant_id", "memberships", ["tenant_id"])
    op.create_index("ix_audit_log_tenant_id", "audit_log", ["tenant_id"])
    op.create_index("ix_audit_log_actor_user_id", "audit_log", ["actor_user_id"])
    op.create_index("ix_audit_log_ts", "audit_log", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_ts", "audit_log")
    op.drop_index("ix_audit_log_actor_user_id", "audit_log")
    op.drop_index("ix_audit_log_tenant_id", "audit_log")
    op.drop_index("ix_memberships_tenant_id", "memberships")
    op.drop_index("ix_sessions_expires_at", "sessions")
    op.drop_index("ix_sessions_user_id", "sessions")
    for table in reversed(_TIMESTAMP_TABLES):
        op.drop_column(table, "updated_at")
```

- [ ] **Step 5: Apply + verify, run test**

Run:
```bash
cd backend
ALEMBIC_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms .venv/bin/alembic upgrade head
ALEMBIC_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms .venv/bin/alembic check
.venv/bin/python -m pytest tests/test_migration_0004.py -v
```
Expected: upgrade ok; `alembic check` → "No new upgrade operations detected." (models match); test PASS.

- [ ] **Step 6: Run full suite + commit**

Run: `cd backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest -q`
Expected: all green (12 passed).

```bash
git add backend/app/models/base.py backend/migrations/versions/0004_indexes_updated_at.py backend/tests/test_migration_0004.py
git commit -m "feat(backend): migration 0004 — indexes + updated_at"
```

---

## Task 2: Password hashing (argon2)

**Files:**
- Create: `backend/app/core/security.py`
- Test: `backend/tests/test_security.py`

- [ ] **Step 1: Write the failing test**

`backend/tests/test_security.py`:
```python
from app.core.security import hash_password, verify_password


def test_hash_then_verify_roundtrip():
    h = hash_password("s3cret-pw")
    assert h != "s3cret-pw"  # not in plaintext
    assert verify_password("s3cret-pw", h) is True
    assert verify_password("wrong", h) is False


def test_two_hashes_of_same_password_differ():
    assert hash_password("x") != hash_password("x")  # random salt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_security.py -v`
Expected: FAIL — `ModuleNotFoundError: app.core.security`.

- [ ] **Step 3: Implement**

`backend/app/core/security.py`:
```python
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_security.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/security.py backend/tests/test_security.py
git commit -m "feat(backend): argon2 password hashing (security.py)"
```

---

## Task 3: Test factories + auth schemas

**Files:**
- Create: `backend/tests/factories.py`
- Create: `backend/app/schemas/__init__.py`, `backend/app/schemas/auth.py`

Factories create data in tests by connecting as **owner** (to write control-plane), reusing the `db_engine` from conftest (Milestone A). Schemas are the pydantic I/O models.

- [ ] **Step 1: Write the schemas**

`backend/app/schemas/__init__.py`: (empty)

`backend/app/schemas/auth.py`:
```python
import uuid

from pydantic import BaseModel, EmailStr


class SetupIn(BaseModel):
    email: EmailStr
    name: str
    password: str


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class MeOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    name: str
    is_superadmin: bool
```
(`EmailStr` requires `email-validator`; add `"email-validator>=2.0"` to `[project.dependencies]` in `backend/pyproject.toml` and `.venv/bin/pip install -e ".[dev]"`.)

- [ ] **Step 2: Write the factories**

`backend/tests/factories.py`:
```python
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.membership import Membership
from app.models.tenant import Tenant
from app.models.user import User


async def make_user(
    session: AsyncSession,
    *,
    email: str,
    password: str = "pw",
    is_superadmin: bool = False,
    name: str = "Test User",
) -> User:
    user = User(
        email=email,
        name=name,
        password_hash=hash_password(password),
        is_superadmin=is_superadmin,
    )
    session.add(user)
    await session.flush()
    return user


async def make_tenant(session: AsyncSession, *, slug: str, name: str = "Tenant") -> Tenant:
    tenant = Tenant(name=name, slug=slug)
    session.add(tenant)
    await session.flush()
    return tenant


async def make_membership(
    session: AsyncSession, *, user_id: uuid.UUID, tenant_id: uuid.UUID, role: str
) -> Membership:
    m = Membership(user_id=user_id, tenant_id=tenant_id, role=role)
    session.add(m)
    await session.flush()
    return m
```

- [ ] **Step 3: Verify import + commit**

Run: `cd backend && .venv/bin/python -c "import tests.factories, app.schemas.auth; print('ok')"`
Expected: `ok`.

```bash
git add backend/app/schemas backend/tests/factories.py backend/pyproject.toml
git commit -m "feat(backend): auth schemas + test factories (+ email-validator)"
```

---

## Task 4: Test app client + session helper

To test HTTP endpoints we need an ASGI `AsyncClient` that shares the same test engine/DB and applies migrations/RLS. We extend the conftest with a client that overrides `get_session` to point to the test DB.

**Files:**
- Modify: `backend/tests/conftest.py`
- Modify: `backend/app/main.py` (router mounting happens in subsequent tasks; here we just ensure `app` uses `get_session` as an overridable dependency)

- [ ] **Step 1: Add the `api_client` fixture**

Add to `backend/tests/conftest.py`:
```python
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import get_session
from app.main import app as fastapi_app


@pytest.fixture
async def api_client(db_engine):
    """ASGI client with get_session overridden to point to the test DB (owner role)."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def _override_get_session():
        async with factory() as s:
            yield s

    fastapi_app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=fastapi_app)
    # base_url https:// so httpx stores `secure=True` cookies (ASGITransport does no real TLS).
    async with AsyncClient(transport=transport, base_url="https://test") as c:
        yield c
    fastapi_app.dependency_overrides.clear()
```
NOTE: this fixture connects as the test DB owner (the test DB URL uses `opngms`). RLS-on-devices
isn't exercised here (Milestone B touches control-plane tables only); device RLS keeps its
dedicated tests from Milestone A. The genuine app-role isolation test from Milestone A stays.

- [ ] **Step 2: Verify the app still imports + suite green; commit**

Run: `cd backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest -q`
Expected: still green (no new tests yet; the fixture is unused so far).

```bash
git add backend/tests/conftest.py
git commit -m "test(backend): ASGI api_client fixture with get_session override"
```

---

## Task 5: One-time setup endpoint (first superadmin)

**Files:**
- Create: `backend/app/api/__init__.py`, `backend/app/api/setup.py`
- Create: `backend/app/repositories/user.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_setup_endpoint.py`

- [ ] **Step 1: Write the failing test**

`backend/tests/test_setup_endpoint.py`:
```python
async def test_setup_creates_first_superadmin(api_client):
    resp = await api_client.post(
        "/api/setup",
        json={"email": "admin@x.io", "name": "Admin", "password": "pw12345"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "admin@x.io"
    assert body["is_superadmin"] is True


async def test_setup_disabled_once_a_user_exists(api_client):
    first = await api_client.post(
        "/api/setup",
        json={"email": "a@x.io", "name": "A", "password": "pw12345"},
    )
    assert first.status_code == 201
    second = await api_client.post(
        "/api/setup",
        json={"email": "b@x.io", "name": "B", "password": "pw12345"},
    )
    assert second.status_code == 409
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && TEST_DATABASE_URL=...opngms_test .venv/bin/python -m pytest tests/test_setup_endpoint.py -v`
Expected: FAIL — 404 (route not mounted yet).

- [ ] **Step 3: Implement the user repository**

`backend/app/repositories/user.py`:
```python
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def count(self) -> int:
        result = await self.session.execute(select(func.count()).select_from(User))
        return int(result.scalar_one())

    async def get_by_email(self, email: str) -> User | None:
        result = await self.session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def add(self, user: User) -> User:
        self.session.add(user)
        await self.session.flush()
        return user

    async def list(self) -> list[User]:
        result = await self.session.execute(select(User).order_by(User.email))
        return list(result.scalars().all())
```

- [ ] **Step 4: Implement the setup router**

`backend/app/api/__init__.py`: (empty)

`backend/app/api/setup.py`:
```python
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import hash_password
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.auth import MeOut, SetupIn

router = APIRouter(prefix="/api", tags=["setup"])


@router.post("/setup", response_model=MeOut, status_code=status.HTTP_201_CREATED)
async def setup(payload: SetupIn, session: AsyncSession = Depends(get_session)) -> User:
    repo = UserRepository(session)
    if await repo.count() > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Setup already complete: at least one user exists.",
        )
    user = User(
        email=payload.email,
        name=payload.name,
        password_hash=hash_password(payload.password),
        is_superadmin=True,
    )
    await repo.add(user)
    await session.commit()
    return user
```

- [ ] **Step 5: Mount the router**

In `backend/app/main.py`, import and include:
```python
from app.api.setup import router as setup_router

app.include_router(setup_router)
```

- [ ] **Step 6: Run tests to verify pass**

Run: `cd backend && TEST_DATABASE_URL=...opngms_test .venv/bin/python -m pytest tests/test_setup_endpoint.py -v`
Expected: PASS (2 passed).

- [ ] **Step 7: Commit**

```bash
git add backend/app/api backend/app/repositories/user.py backend/app/main.py backend/tests/test_setup_endpoint.py
git commit -m "feat(backend): /api/setup one-time endpoint for the first superadmin"
```

---

## Task 6: Sessions + login/logout/me + `get_current_user`

**Files:**
- Create: `backend/app/services/auth.py`
- Create: `backend/app/api/auth.py`
- Modify: `backend/app/core/deps.py` (create), `backend/app/main.py`
- Test: `backend/tests/test_auth.py`

- [ ] **Step 1: Write the failing test**

`backend/tests/test_auth.py`:
```python
async def _setup_admin(api_client):
    await api_client.post(
        "/api/setup",
        json={"email": "admin@x.io", "name": "Admin", "password": "pw12345"},
    )


async def test_login_sets_cookie_and_me_returns_user(api_client):
    await _setup_admin(api_client)
    resp = await api_client.post(
        "/api/login", json={"email": "admin@x.io", "password": "pw12345"}
    )
    assert resp.status_code == 200
    assert "opngms_session" in resp.cookies
    me = await api_client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["email"] == "admin@x.io"


async def test_login_wrong_password_401(api_client):
    await _setup_admin(api_client)
    resp = await api_client.post(
        "/api/login", json={"email": "admin@x.io", "password": "nope"}
    )
    assert resp.status_code == 401


async def test_me_without_session_401(api_client):
    resp = await api_client.get("/api/me")
    assert resp.status_code == 401


async def test_logout_clears_session(api_client):
    await _setup_admin(api_client)
    await api_client.post("/api/login", json={"email": "admin@x.io", "password": "pw12345"})
    out = await api_client.post("/api/logout", headers={"X-OPNGMS-CSRF": "1"})
    assert out.status_code == 204
    me = await api_client.get("/api/me")
    assert me.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && TEST_DATABASE_URL=...opngms_test .venv/bin/python -m pytest tests/test_auth.py -v`
Expected: FAIL — login route 404.

- [ ] **Step 3: Implement the auth service**

`backend/app/services/auth.py`:
```python
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_password
from app.models.session import Session
from app.models.user import User


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

    async def create_session(self, user: User, ttl_hours: int) -> Session:
        now = datetime.now(timezone.utc)
        sess = Session(
            user_id=user.id, expires_at=now + timedelta(hours=ttl_hours)
        )
        self.session.add(sess)
        user.last_login = now
        await self.session.flush()
        return sess

    async def get_user_for_session(self, session_id: uuid.UUID) -> User | None:
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            select(Session).where(Session.id == session_id)
        )
        sess = result.scalar_one_or_none()
        if sess is None or sess.expires_at <= now:
            return None
        return await self.session.get(User, sess.user_id)

    async def delete_session(self, session_id: uuid.UUID) -> None:
        await self.session.execute(delete(Session).where(Session.id == session_id))
```

- [ ] **Step 4: Implement `get_current_user` dependency**

`backend/app/core/deps.py`:
```python
import uuid

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models.user import User
from app.services.auth import AuthService

SESSION_COOKIE = "opngms_session"


async def get_current_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> User:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        session_id = uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    user = await AuthService(session).get_user_for_session(session_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    return user
```

- [ ] **Step 5: Implement the auth router**

`backend/app/api/auth.py`:
```python
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import SESSION_COOKIE, get_current_user
from app.models.user import User
from app.schemas.auth import LoginIn, MeOut
from app.services.auth import AuthService

router = APIRouter(prefix="/api", tags=["auth"])


@router.post("/login", response_model=MeOut)
async def login(
    payload: LoginIn, response: Response, session: AsyncSession = Depends(get_session)
) -> User:
    svc = AuthService(session)
    user = await svc.authenticate(payload.email, payload.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )
    settings = get_settings()
    sess = await svc.create_session(user, settings.session_ttl_hours)
    await session.commit()
    response.set_cookie(
        SESSION_COOKIE,
        str(sess.id),
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=settings.session_ttl_hours * 3600,
    )
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        import uuid

        try:
            await AuthService(session).delete_session(uuid.UUID(raw))
            await session.commit()
        except ValueError:
            pass
    response.delete_cookie(SESSION_COOKIE)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=MeOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user
```

- [ ] **Step 6: Mount the router**

In `backend/app/main.py` add:
```python
from app.api.auth import router as auth_router

app.include_router(auth_router)
```

- [ ] **Step 7: Run tests + commit**

Run: `cd backend && TEST_DATABASE_URL=...opngms_test .venv/bin/python -m pytest tests/test_auth.py -v`
Expected: PASS (4 passed).
NOTE: `secure=True` cookies are still returned over the ASGI transport (httpx records them); the tests assert on the cookie jar, which works.

```bash
git add backend/app/services/auth.py backend/app/core/deps.py backend/app/api/auth.py backend/app/main.py backend/tests/test_auth.py
git commit -m "feat(backend): sessions + login/logout/me + get_current_user"
```

---

## Task 7: CSRF enforcement on mutations

**Files:**
- Modify: `backend/app/core/deps.py`
- Test: `backend/tests/test_csrf.py`

Strategy: `SameSite=Lax` cookie (already set) + mandatory custom header `X-OPNGMS-CSRF` on all
state-changing requests (POST/PUT/PATCH/DELETE), EXCEPT `/api/setup` and `/api/login` (which
have no session yet / are bootstrap). The `enforce_csrf` dependency is applied to protected routers.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_csrf.py`:
```python
async def _login(api_client):
    await api_client.post(
        "/api/setup", json={"email": "a@x.io", "name": "A", "password": "pw12345"}
    )
    await api_client.post("/api/login", json={"email": "a@x.io", "password": "pw12345"})


async def test_mutation_without_csrf_header_rejected(api_client):
    await _login(api_client)
    # logout is a protected mutation: without header -> 403
    resp = await api_client.post("/api/logout")
    assert resp.status_code == 403


async def test_mutation_with_csrf_header_allowed(api_client):
    await _login(api_client)
    resp = await api_client.post("/api/logout", headers={"X-OPNGMS-CSRF": "1"})
    assert resp.status_code == 204
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && TEST_DATABASE_URL=...opngms_test .venv/bin/python -m pytest tests/test_csrf.py -v`
Expected: FAIL — logout currently returns 204 without the header.

- [ ] **Step 3: Implement `enforce_csrf` and apply it to logout**

Add to `backend/app/core/deps.py`:
```python
CSRF_HEADER = "X-OPNGMS-CSRF"


async def enforce_csrf(request: Request) -> None:
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if not request.headers.get(CSRF_HEADER):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing CSRF header",
            )
```
Apply it to the logout route in `backend/app/api/auth.py` by adding it to the dependencies:
```python
@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(enforce_csrf)],
)
```
(import `enforce_csrf` from `app.core.deps`).

- [ ] **Step 4: Run tests + commit**

Run: `cd backend && TEST_DATABASE_URL=...opngms_test .venv/bin/python -m pytest tests/test_csrf.py tests/test_auth.py -v`
Expected: PASS (note: `test_logout_clears_session` already sends the header).

```bash
git add backend/app/core/deps.py backend/app/api/auth.py backend/tests/test_csrf.py
git commit -m "feat(backend): CSRF enforcement (custom header on mutations)"
```

---

## Task 8: RBAC — matrix + `can()`

**Files:**
- Create: `backend/app/core/rbac.py`
- Test: `backend/tests/test_rbac_matrix.py`

- [ ] **Step 1: Write the failing test**

`backend/tests/test_rbac_matrix.py`:
```python
import pytest

from app.core.rbac import (
    OPERATOR,
    READ_ONLY,
    TENANT_ADMIN,
    Action,
    can,
)


@pytest.mark.parametrize(
    "is_superadmin,role,action,expected",
    [
        # org-level: superadmin only
        (True, None, Action.TENANT_MANAGE, True),
        (False, TENANT_ADMIN, Action.TENANT_MANAGE, False),
        (False, TENANT_ADMIN, Action.USER_MANAGE, False),
        (True, None, Action.USER_MANAGE, True),
        # membership: superadmin + tenant_admin
        (False, TENANT_ADMIN, Action.MEMBERSHIP_MANAGE, True),
        (False, OPERATOR, Action.MEMBERSHIP_MANAGE, False),
        (True, None, Action.MEMBERSHIP_MANAGE, True),
        # device.view: all tenant roles
        (False, READ_ONLY, Action.DEVICE_VIEW, True),
        # device.write: tenant_admin + operator
        (False, OPERATOR, Action.DEVICE_WRITE, True),
        (False, READ_ONLY, Action.DEVICE_WRITE, False),
        # audit.view: all
        (False, READ_ONLY, Action.AUDIT_VIEW, True),
    ],
)
def test_permission_matrix(is_superadmin, role, action, expected):
    assert can(is_superadmin=is_superadmin, role=role, action=action) is expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_rbac_matrix.py -v`
Expected: FAIL — `ModuleNotFoundError: app.core.rbac`.

- [ ] **Step 3: Implement**

`backend/app/core/rbac.py`:
```python
import enum

# Per-tenant roles (assigned via Membership). 'superadmin' is a user-level flag.
TENANT_ADMIN = "tenant_admin"
OPERATOR = "operator"
READ_ONLY = "read_only"
TENANT_ROLES = {TENANT_ADMIN, OPERATOR, READ_ONLY}


class Action(str, enum.Enum):
    # org-level (superadmin only)
    TENANT_MANAGE = "tenant.manage"
    USER_MANAGE = "user.manage"
    # tenant-level
    MEMBERSHIP_MANAGE = "membership.manage"
    DEVICE_VIEW = "device.view"
    DEVICE_WRITE = "device.write"
    AUDIT_VIEW = "audit.view"


# Org-level actions: allowed ONLY to superadmin (no per-tenant role grants them).
_ORG_ACTIONS = {Action.TENANT_MANAGE, Action.USER_MANAGE}

# Tenant-level actions -> roles that grant them (besides superadmin, always allowed).
_TENANT_MATRIX: dict[Action, set[str]] = {
    Action.MEMBERSHIP_MANAGE: {TENANT_ADMIN},
    Action.DEVICE_VIEW: {TENANT_ADMIN, OPERATOR, READ_ONLY},
    Action.DEVICE_WRITE: {TENANT_ADMIN, OPERATOR},
    Action.AUDIT_VIEW: {TENANT_ADMIN, OPERATOR, READ_ONLY},
}


def can(*, is_superadmin: bool, role: str | None, action: Action) -> bool:
    if is_superadmin:
        return True
    if action in _ORG_ACTIONS:
        return False
    return role in _TENANT_MATRIX.get(action, set())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_rbac_matrix.py -v`
Expected: PASS (all parametrized cases).

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/rbac.py backend/tests/test_rbac_matrix.py
git commit -m "feat(backend): RBAC matrix + can() (4 roles)"
```

---

## Task 9: Audit service

**Files:**
- Create: `backend/app/services/audit.py`
- Test: `backend/tests/test_audit.py`

- [ ] **Step 1: Write the failing test**

`backend/tests/test_audit.py`:
```python
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.audit import AuditLog
from app.services.audit import AuditService


async def test_record_writes_audit_row(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    actor = uuid.uuid4()
    async with factory() as s:
        await AuditService(s).record(
            actor_user_id=actor,
            tenant_id=None,
            action="tenant.create",
            target_type="tenant",
            target_id="abc",
            ip="1.2.3.4",
            details={"name": "X"},
        )
        await s.commit()
    async with factory() as s:
        rows = (await s.execute(select(AuditLog))).scalars().all()
        assert any(
            r.action == "tenant.create" and r.actor_user_id == actor and r.details == {"name": "X"}
            for r in rows
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && TEST_DATABASE_URL=...opngms_test .venv/bin/python -m pytest tests/test_audit.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.audit`.

- [ ] **Step 3: Implement**

`backend/app/services/audit.py`:
```python
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        actor_user_id: uuid.UUID | None,
        tenant_id: uuid.UUID | None,
        action: str,
        target_type: str | None = None,
        target_id: str | None = None,
        ip: str | None = None,
        details: dict | None = None,
    ) -> None:
        self.session.add(
            AuditLog(
                actor_user_id=actor_user_id,
                tenant_id=tenant_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                ip=ip,
                details=details or {},
            )
        )
        await self.session.flush()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && TEST_DATABASE_URL=...opngms_test .venv/bin/python -m pytest tests/test_audit.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/audit.py backend/tests/test_audit.py
git commit -m "feat(backend): AuditService.record"
```

---

## Task 10: Tenants API (superadmin) + `require_org` dependency

**Files:**
- Create: `backend/app/schemas/tenant.py`, `backend/app/repositories/tenant.py`, `backend/app/api/tenants.py`
- Modify: `backend/app/core/deps.py`, `backend/app/main.py`
- Test: `backend/tests/test_tenants_api.py`

- [ ] **Step 1: Write the failing test**

`backend/tests/test_tenants_api.py`:
```python
async def _login_superadmin(api_client):
    await api_client.post(
        "/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345"}
    )
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345"})


CSRF = {"X-OPNGMS-CSRF": "1"}


async def test_superadmin_can_create_and_list_tenants(api_client):
    await _login_superadmin(api_client)
    created = await api_client.post(
        "/api/tenants", json={"name": "Client A", "slug": "client-a"}, headers=CSRF
    )
    assert created.status_code == 201
    assert created.json()["slug"] == "client-a"
    listed = await api_client.get("/api/tenants")
    assert listed.status_code == 200
    assert any(t["slug"] == "client-a" for t in listed.json())


async def test_non_superadmin_cannot_create_tenant(api_client, db_engine):
    # create a non-superadmin user directly, then login
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from tests.factories import make_user

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await make_user(s, email="op@x.io", password="pw12345", is_superadmin=False)
        await s.commit()
    await api_client.post("/api/login", json={"email": "op@x.io", "password": "pw12345"})
    resp = await api_client.post(
        "/api/tenants", json={"name": "X", "slug": "x"}, headers=CSRF
    )
    assert resp.status_code == 403


async def test_unauthenticated_cannot_list_tenants(api_client):
    resp = await api_client.get("/api/tenants")
    assert resp.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && TEST_DATABASE_URL=...opngms_test .venv/bin/python -m pytest tests/test_tenants_api.py -v`
Expected: FAIL — route 404.

- [ ] **Step 3: Implement schema + repository + `require_org` dependency**

`backend/app/schemas/tenant.py`:
```python
import uuid

from pydantic import BaseModel


class TenantIn(BaseModel):
    name: str
    slug: str
    note: str | None = None


class TenantOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    status: str
    note: str | None

    model_config = {"from_attributes": True}
```

`backend/app/repositories/tenant.py`:
```python
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant


class TenantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, tenant: Tenant) -> Tenant:
        self.session.add(tenant)
        await self.session.flush()
        return tenant

    async def get(self, tenant_id: uuid.UUID) -> Tenant | None:
        return await self.session.get(Tenant, tenant_id)

    async def list(self) -> list[Tenant]:
        result = await self.session.execute(select(Tenant).order_by(Tenant.slug))
        return list(result.scalars().all())
```

Add to `backend/app/core/deps.py` a dependency factory for org-level actions:
```python
from app.core.rbac import Action, can


def require_org(action: Action):
    async def _dep(user: "User" = Depends(get_current_user)) -> "User":
        if not can(is_superadmin=user.is_superadmin, role=None, action=action):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied"
            )
        return user

    return _dep
```
(Ensure `User` import is available; it already is in deps.py.)

- [ ] **Step 4: Implement the tenants router**

`backend/app/api/tenants.py`:
```python
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import enforce_csrf, require_org
from app.core.rbac import Action
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.tenant import TenantRepository
from app.schemas.tenant import TenantIn, TenantOut
from app.services.audit import AuditService

router = APIRouter(prefix="/api/tenants", tags=["tenants"])


@router.get("", response_model=list[TenantOut])
async def list_tenants(
    user: User = Depends(require_org(Action.TENANT_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> list[Tenant]:
    return await TenantRepository(session).list()


@router.post(
    "",
    response_model=TenantOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_csrf)],
)
async def create_tenant(
    payload: TenantIn,
    request: Request,
    user: User = Depends(require_org(Action.TENANT_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> Tenant:
    repo = TenantRepository(session)
    tenant = await repo.add(Tenant(name=payload.name, slug=payload.slug, note=payload.note))
    await AuditService(session).record(
        actor_user_id=user.id,
        tenant_id=tenant.id,
        action="tenant.create",
        target_type="tenant",
        target_id=str(tenant.id),
        ip=request.client.host if request.client else None,
        details={"slug": tenant.slug},
    )
    await session.commit()
    return tenant
```

- [ ] **Step 5: Mount the router**

In `backend/app/main.py`:
```python
from app.api.tenants import router as tenants_router

app.include_router(tenants_router)
```

- [ ] **Step 6: Run tests + commit**

Run: `cd backend && TEST_DATABASE_URL=...opngms_test .venv/bin/python -m pytest tests/test_tenants_api.py -v`
Expected: PASS (3 passed).

```bash
git add backend/app/schemas/tenant.py backend/app/repositories/tenant.py backend/app/api/tenants.py backend/app/core/deps.py backend/app/main.py backend/tests/test_tenants_api.py
git commit -m "feat(backend): tenants API (superadmin) + require_org + audit"
```

---

## Task 11: Users API (superadmin)

**Files:**
- Create: `backend/app/schemas/user.py`, `backend/app/api/users.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_users_api.py`

- [ ] **Step 1: Write the failing test**

`backend/tests/test_users_api.py`:
```python
async def _login_superadmin(api_client):
    await api_client.post(
        "/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345"}
    )
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345"})


CSRF = {"X-OPNGMS-CSRF": "1"}


async def test_superadmin_creates_user(api_client):
    await _login_superadmin(api_client)
    resp = await api_client.post(
        "/api/users",
        json={"email": "u@x.io", "name": "U", "password": "pw12345", "is_superadmin": False},
        headers=CSRF,
    )
    assert resp.status_code == 201
    assert resp.json()["email"] == "u@x.io"
    listed = await api_client.get("/api/users")
    assert any(u["email"] == "u@x.io" for u in listed.json())


async def test_create_user_duplicate_email_409(api_client):
    await _login_superadmin(api_client)
    body = {"email": "dup@x.io", "name": "D", "password": "pw12345", "is_superadmin": False}
    assert (await api_client.post("/api/users", json=body, headers=CSRF)).status_code == 201
    assert (await api_client.post("/api/users", json=body, headers=CSRF)).status_code == 409
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && TEST_DATABASE_URL=...opngms_test .venv/bin/python -m pytest tests/test_users_api.py -v`
Expected: FAIL — route 404.

- [ ] **Step 3: Implement schema + router**

`backend/app/schemas/user.py`:
```python
import uuid

from pydantic import BaseModel, EmailStr


class UserCreateIn(BaseModel):
    email: EmailStr
    name: str
    password: str
    is_superadmin: bool = False


class UserOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    name: str
    is_superadmin: bool
    status: str

    model_config = {"from_attributes": True}
```

`backend/app/api/users.py`:
```python
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import enforce_csrf, require_org
from app.core.rbac import Action
from app.core.security import hash_password
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.user import UserCreateIn, UserOut
from app.services.audit import AuditService

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("", response_model=list[UserOut])
async def list_users(
    user: User = Depends(require_org(Action.USER_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> list[User]:
    return await UserRepository(session).list()


@router.post(
    "",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_csrf)],
)
async def create_user(
    payload: UserCreateIn,
    request: Request,
    actor: User = Depends(require_org(Action.USER_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> User:
    repo = UserRepository(session)
    if await repo.get_by_email(payload.email) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already in use")
    new_user = await repo.add(
        User(
            email=payload.email,
            name=payload.name,
            password_hash=hash_password(payload.password),
            is_superadmin=payload.is_superadmin,
        )
    )
    await AuditService(session).record(
        actor_user_id=actor.id,
        tenant_id=None,
        action="user.create",
        target_type="user",
        target_id=str(new_user.id),
        ip=request.client.host if request.client else None,
        details={"email": new_user.email, "is_superadmin": new_user.is_superadmin},
    )
    await session.commit()
    return new_user
```

- [ ] **Step 4: Mount the router**

In `backend/app/main.py`:
```python
from app.api.users import router as users_router

app.include_router(users_router)
```

- [ ] **Step 5: Run tests + commit**

Run: `cd backend && TEST_DATABASE_URL=...opngms_test .venv/bin/python -m pytest tests/test_users_api.py -v`
Expected: PASS (2 passed).

```bash
git add backend/app/schemas/user.py backend/app/api/users.py backend/app/main.py backend/tests/test_users_api.py
git commit -m "feat(backend): users API (superadmin) + audit"
```

---

## Task 12: Tenant request-context + Memberships API (RLS wiring)

**Files:**
- Create: `backend/app/schemas/membership.py`, `backend/app/repositories/membership.py`, `backend/app/api/memberships.py`
- Modify: `backend/app/core/deps.py`, `backend/app/main.py`
- Test: `backend/tests/test_tenant_context.py`, `backend/tests/test_memberships_api.py`

This is the task that **wires `app.current_tenant`**: the `tenant_context` dependency resolves the
tenant from the path, authorises (superadmin or membership), sets the GUC (RLS active) and provides
the effective role for RBAC checks.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_tenant_context.py`:
```python
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.factories import make_membership, make_tenant, make_user

CSRF = {"X-OPNGMS-CSRF": "1"}


async def _seed(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="t1")
        admin = await make_user(s, email="ta@x.io", password="pw12345")
        await make_membership(s, user_id=admin.id, tenant_id=t.id, role="tenant_admin")
        outsider = await make_user(s, email="out@x.io", password="pw12345")
        await s.commit()
        return t.id


async def test_member_can_access_tenant_scope(api_client, db_engine):
    tenant_id = await _seed(db_engine)
    await api_client.post("/api/login", json={"email": "ta@x.io", "password": "pw12345"})
    # membership listing is a tenant-scoped route: the tenant_admin member can access it
    resp = await api_client.get(f"/api/tenants/{tenant_id}/memberships")
    assert resp.status_code == 200


async def test_non_member_denied_tenant_scope(api_client, db_engine):
    tenant_id = await _seed(db_engine)
    await api_client.post("/api/login", json={"email": "out@x.io", "password": "pw12345"})
    resp = await api_client.get(f"/api/tenants/{tenant_id}/memberships")
    assert resp.status_code == 403


async def test_unknown_tenant_404(api_client, db_engine):
    await _seed(db_engine)
    await api_client.post("/api/login", json={"email": "ta@x.io", "password": "pw12345"})
    resp = await api_client.get(f"/api/tenants/{uuid.uuid4()}/memberships")
    assert resp.status_code == 404
```

`backend/tests/test_memberships_api.py`:
```python
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.factories import make_tenant, make_user

CSRF = {"X-OPNGMS-CSRF": "1"}


async def _seed_superadmin_and_tenant(api_client, db_engine):
    await api_client.post(
        "/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345"}
    )
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        u = await make_user(s, email="member@x.io", password="pw12345")
        await s.commit()
        return t.id, u.id


async def test_superadmin_assigns_membership(api_client, db_engine):
    tenant_id, user_id = await _seed_superadmin_and_tenant(api_client, db_engine)
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345"})
    resp = await api_client.post(
        f"/api/tenants/{tenant_id}/memberships",
        json={"user_id": str(user_id), "role": "operator"},
        headers=CSRF,
    )
    assert resp.status_code == 201
    assert resp.json()["role"] == "operator"
    listed = await api_client.get(f"/api/tenants/{tenant_id}/memberships")
    assert any(m["user_id"] == str(user_id) for m in listed.json())


async def test_invalid_role_rejected(api_client, db_engine):
    tenant_id, user_id = await _seed_superadmin_and_tenant(api_client, db_engine)
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345"})
    resp = await api_client.post(
        f"/api/tenants/{tenant_id}/memberships",
        json={"user_id": str(user_id), "role": "wizard"},
        headers=CSRF,
    )
    assert resp.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && TEST_DATABASE_URL=...opngms_test .venv/bin/python -m pytest tests/test_tenant_context.py tests/test_memberships_api.py -v`
Expected: FAIL — routes 404.

- [ ] **Step 3: Implement the tenant-context dependency**

Add to `backend/app/core/deps.py`:
```python
import uuid as _uuid
from dataclasses import dataclass

from sqlalchemy import select

from app.core.db import set_tenant_context
from app.core.rbac import Action, can
from app.models.membership import Membership
from app.models.tenant import Tenant


@dataclass
class TenantContext:
    tenant: Tenant
    user: User
    role: str | None  # None for superadmin without membership


async def tenant_context(
    tenant_id: _uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TenantContext:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    role: str | None = None
    if not user.is_superadmin:
        result = await session.execute(
            select(Membership).where(
                Membership.user_id == user.id, Membership.tenant_id == tenant_id
            )
        )
        membership = result.scalar_one_or_none()
        if membership is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Tenant access denied"
            )
        role = membership.role
    # RLS wiring: set app.current_tenant for this transaction.
    await set_tenant_context(session, tenant_id)
    return TenantContext(tenant=tenant, user=user, role=role)


def require_tenant(action: Action):
    async def _dep(ctx: TenantContext = Depends(tenant_context)) -> TenantContext:
        if not can(is_superadmin=ctx.user.is_superadmin, role=ctx.role, action=action):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied"
            )
        return ctx

    return _dep
```

- [ ] **Step 4: Implement membership schema + repository**

`backend/app/schemas/membership.py`:
```python
import uuid
from typing import Literal

from pydantic import BaseModel


class MembershipIn(BaseModel):
    user_id: uuid.UUID
    role: Literal["tenant_admin", "operator", "read_only"]


class MembershipOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: str

    model_config = {"from_attributes": True}
```

`backend/app/repositories/membership.py`:
```python
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.membership import Membership


class MembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, membership: Membership) -> Membership:
        self.session.add(membership)
        await self.session.flush()
        return membership

    async def list_for_tenant(self, tenant_id: uuid.UUID) -> list[Membership]:
        result = await self.session.execute(
            select(Membership).where(Membership.tenant_id == tenant_id)
        )
        return list(result.scalars().all())
```

- [ ] **Step 5: Implement the memberships router**

`backend/app/api/memberships.py`:
```python
import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import TenantContext, enforce_csrf, require_tenant
from app.core.rbac import Action
from app.models.membership import Membership
from app.repositories.membership import MembershipRepository
from app.schemas.membership import MembershipIn, MembershipOut
from app.services.audit import AuditService

router = APIRouter(prefix="/api/tenants/{tenant_id}/memberships", tags=["memberships"])


@router.get("", response_model=list[MembershipOut])
async def list_memberships(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.MEMBERSHIP_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> list[Membership]:
    return await MembershipRepository(session).list_for_tenant(tenant_id)


@router.post(
    "",
    response_model=MembershipOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_csrf)],
)
async def create_membership(
    tenant_id: uuid.UUID,
    payload: MembershipIn,
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.MEMBERSHIP_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> Membership:
    repo = MembershipRepository(session)
    membership = await repo.add(
        Membership(user_id=payload.user_id, tenant_id=tenant_id, role=payload.role)
    )
    await AuditService(session).record(
        actor_user_id=ctx.user.id,
        tenant_id=tenant_id,
        action="membership.create",
        target_type="membership",
        target_id=str(membership.id),
        ip=request.client.host if request.client else None,
        details={"user_id": str(payload.user_id), "role": payload.role},
    )
    await session.commit()
    return membership
```

- [ ] **Step 6: Mount the router**

In `backend/app/main.py`:
```python
from app.api.memberships import router as memberships_router

app.include_router(memberships_router)
```

- [ ] **Step 7: Run tests + commit**

Run: `cd backend && TEST_DATABASE_URL=...opngms_test .venv/bin/python -m pytest tests/test_tenant_context.py tests/test_memberships_api.py -v`
Expected: PASS (5 passed).

```bash
git add backend/app/schemas/membership.py backend/app/repositories/membership.py backend/app/api/memberships.py backend/app/core/deps.py backend/app/main.py backend/tests/test_tenant_context.py backend/tests/test_memberships_api.py
git commit -m "feat(backend): tenant request-context (RLS wiring) + memberships API"
```

---

## Task 13: End-to-end integration + suite verification

**Files:**
- Create: `backend/tests/test_b_integration.py`

- [ ] **Step 1: Write the integration test**

`backend/tests/test_b_integration.py`:
```python
CSRF = {"X-OPNGMS-CSRF": "1"}


async def test_full_admin_flow(api_client):
    # 1. setup first superadmin
    await api_client.post(
        "/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345"}
    )
    # 2. login superadmin
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345"})
    # 3. create tenant
    t = await api_client.post(
        "/api/tenants", json={"name": "Acme", "slug": "acme"}, headers=CSRF
    )
    tenant_id = t.json()["id"]
    # 4. create operator user
    u = await api_client.post(
        "/api/users",
        json={"email": "op@x.io", "name": "Op", "password": "pw12345", "is_superadmin": False},
        headers=CSRF,
    )
    user_id = u.json()["id"]
    # 5. assign operator membership
    m = await api_client.post(
        f"/api/tenants/{tenant_id}/memberships",
        json={"user_id": user_id, "role": "operator"},
        headers=CSRF,
    )
    assert m.status_code == 201
    # 6. logout superadmin, login operator
    await api_client.post("/api/logout", headers=CSRF)
    await api_client.post("/api/login", json={"email": "op@x.io", "password": "pw12345"})
    # 7. operator CANNOT create tenant (org-level)
    denied = await api_client.post(
        "/api/tenants", json={"name": "X", "slug": "x"}, headers=CSRF
    )
    assert denied.status_code == 403
    # 8. but can access their tenant scope (membership) — list memberships
    #    operator does NOT have membership.manage, so 403 expected here (fine-grained RBAC check)
    ms = await api_client.get(f"/api/tenants/{tenant_id}/memberships")
    assert ms.status_code == 403
```

- [ ] **Step 2: Run the whole suite**

Run: `cd backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest -v`
Expected: all green (Milestone A's 11 + Milestone B's new tests).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_b_integration.py
git commit -m "test(backend): Milestone B end-to-end integration"
```

---

## Self-review (spec → task mapping)

- **Spec §8 AuthN** (sessions, argon2, login/logout/me) → Task 2 (argon2), Task 6 (sessions +
  endpoints). Cookie `httpOnly`/`secure`/`SameSite=Lax`.
- **Spec §9 RBAC** (4 roles, matrix, policy layer) → Task 8 (matrix + `can`), Task 10/11
  (`require_org`), Task 12 (`require_tenant` + context). Matrix tested as a parametrized case table.
- **Spec §10 Audit** → Task 9 (`AuditService`), invoked on create tenant/user/membership.
- **Spec §7 RLS wiring** (`app.current_tenant`) → Task 12: `tenant_context` calls
  `set_tenant_context`, authorises membership/superadmin, and Milestone A's RLS becomes
  effective on tenant-scoped routes.
- **Planning decisions:** org-admin CRUD included (Task 10/11/12); first superadmin via
  `/api/setup` one-time (Task 5, user count guard); CSRF via custom header (Task 7).
- **Milestone A technical debt** closed here: indexes sessions/memberships/audit + `updated_at`
  (Task 1).

**Scope notes (outside Milestone B by design):** PATCH/DELETE on tenant/users/membership beyond
create/list are intentionally minimal in this milestone (add when needed, YAGNI); rate-limiting/
login lockout and SSO/2FA remain for later; devices and onboarding are Milestone C. Expired session
rotation/cleanup (job) is tracked but not implemented (index on `expires_at` is ready).

**Placeholder scan:** no TBD/TODO; every step has concrete code or a command.
**Type consistency:** `can(is_superadmin, role, action)`, `Action.*`, `TenantContext(tenant,
user, role)`, `require_org(action)`, `require_tenant(action)`, `enforce_csrf`, `SESSION_COOKIE`,
`get_current_user`, `AuditService.record(...)` used consistently across Tasks 6-13.

---

## Technical debt to address in Milestone C (from final holistic review)

The final review gave **READY WITH MINOR NOTES**: zero Critical/Important issues, solid
authorization model applied consistently across every endpoint. Added during the milestone:
Task 14 (global `IntegrityError`→409 handler) and Task 15 (login/logout audit for spec §10
compliance + removal of duplicate import). Remaining items to track:

1. **No update/delete** for tenant/users/membership — create/list only (YAGNI; add
   PATCH/DELETE when needed).
2. **No rate-limiting / login lockout** — today the only brute-force deterrent is the argon2 cost.
3. **No expired session cleanup job** — checked on read but never deleted
   (the `ix_sessions_expires_at` index is already in place).
4. **No pagination** on list endpoints (`/api/tenants`, `/api/users`,
   `/api/tenants/{id}/memberships`) — full scan, acceptable at current MVP scale.
5. **CSRF by header presence only** (custom-header + `SameSite=Lax` pattern) — consider a
   per-session CSRF token and ensure **CORS** remains closed (not yet configured).
6. **No session rotation / no "logout all sessions"** — multiple concurrent sessions per user
   with no admin revocation path.
7. **`set_tenant_context` is transaction-scoped** (`is_local=true`): sound as long as there is
   one transaction per request. Milestone C, when it adds device queries under RLS, must NOT
   commit mid-request and then query devices (would silently lose context → zero rows, fail-closed,
   but a correctness trap). Add a guard/comment.
