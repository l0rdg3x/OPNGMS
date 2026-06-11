# Login MFA (TOTP) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a TOTP second factor (+ one-time recovery codes) to the OPNGMS login, with self-enrollment, a superadmin-set enforcement policy, superadmin admin-reset of other users, and a host-level break-glass CLI.

**Architecture:** A `kind` on the existing server-side `Session` distinguishes `full` / `mfa_pending` / `mfa_setup`. Login verifies the password as today; if the user has MFA it issues a short-lived `mfa_pending` session and the SPA completes via `/api/login/mfa`; if a policy requires MFA but the user is not enrolled it issues a `mfa_setup` session that can reach only the enrollment endpoints. TOTP secrets are encrypted at rest (`MASTER_KEY`), recovery codes argon2-hashed and one-time. A new `app_settings` key/value table holds the global policy.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy async, Alembic (head `0020` → new `0021`), argon2, **pyotp** (new dep). React 19 + Mantine v9 + openapi-fetch + Vitest.

**Conventions:** venv `/home/l0rdg3x/coding/OPNGMS/backend/.venv/bin/python`; pytest needs `TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test`; CI lint gate is `ruff check app/`. Crypto: `app.core.crypto.encrypt(str)->bytes`, `decrypt(bytes)->str`. Password hashing/verify: `app.core.security.hash_password/verify_password` (argon2). Sessions: `app.services.auth.AuthService` + `app.core.deps`. English everywhere. The test/screenshot DBs build the schema from `Base.metadata.create_all` (conftest), so new models MUST be imported in `app/models/__init__.py`.

---

## BACKEND (→ PR 1)

### Task 1: pyotp dependency

**Files:** Modify `backend/pyproject.toml`

- [ ] **Step 1: Add the dependency** — in `backend/pyproject.toml` `dependencies`, add `"pyotp>=2.9"`. Then install:

```bash
cd backend && .venv/bin/pip install -e . && .venv/bin/python -c "import pyotp; print(pyotp.__version__)"
```
Expected: prints a version ≥ 2.9.

- [ ] **Step 2: Commit**

```bash
cd backend && git add pyproject.toml && git commit -m "build(mfa): add pyotp dependency"
```

---

### Task 2: Models — `UserMfa`, `UserRecoveryCode`, `AppSetting` + `Session.kind`

**Files:**
- Create: `backend/app/models/user_mfa.py`, `backend/app/models/user_recovery_code.py`, `backend/app/models/app_setting.py`
- Modify: `backend/app/models/session.py`, `backend/app/models/__init__.py`
- Test: `backend/tests/test_mfa_models.py`

- [ ] **Step 1: Write the failing test** — `backend/tests/test_mfa_models.py`

```python
from app.models import AppSetting, Session, UserMfa, UserRecoveryCode


def test_models_are_registered_on_metadata():
    from app.models import Base
    tables = set(Base.metadata.tables)
    assert {"user_mfa", "user_recovery_code", "app_settings"} <= tables


def test_session_has_kind_column():
    assert "kind" in Session.__table__.columns
    assert Session.__table__.columns["kind"].default.arg == "full"


def test_mfa_model_columns():
    cols = set(UserMfa.__table__.columns.keys())
    assert {"user_id", "enabled", "totp_secret_enc", "confirmed_at", "last_used_step"} <= cols
    assert "code_hash" in UserRecoveryCode.__table__.columns
    assert {"key", "value"} <= set(AppSetting.__table__.columns.keys())
```

- [ ] **Step 2: Run → fail** — `cd backend && .venv/bin/python -m pytest tests/test_mfa_models.py -v` (ImportError).

- [ ] **Step 3: Implement the models.**

`backend/app/models/user_mfa.py`:
```python
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, LargeBinary
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class UserMfa(TimestampMixin, Base):
    __tablename__ = "user_mfa"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    totp_secret_enc: Mapped[bytes] = mapped_column(LargeBinary)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_used_step: Mapped[int | None] = mapped_column(BigInteger, default=None)
```

`backend/app/models/user_recovery_code.py`:
```python
import uuid

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class UserRecoveryCode(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "user_recovery_code"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    code_hash: Mapped[str] = mapped_column(String)
    used_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=None, nullable=True)
```

`backend/app/models/app_setting.py`:
```python
from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AppSetting(TimestampMixin, Base):
    """Global (non-tenant) key/value settings. Only superadmin-gated endpoints write it."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB)
```

In `backend/app/models/session.py`, add a `kind` column (import `String` is already there):
```python
    kind: Mapped[str] = mapped_column(String(16), default="full", server_default="full")
```
(place it after `user_id`).

In `backend/app/models/__init__.py`, add the imports + `__all__` entries:
```python
from app.models.app_setting import AppSetting  # noqa: F401
from app.models.user_mfa import UserMfa  # noqa: F401
from app.models.user_recovery_code import UserRecoveryCode  # noqa: F401
```
and add `"AppSetting"`, `"UserMfa"`, `"UserRecoveryCode"` to `__all__`.

- [ ] **Step 4: Run → pass.**

- [ ] **Step 5: Alembic migration** — create `backend/alembic/versions/0021_mfa.py`:

```python
"""mfa: session kind + user_mfa + user_recovery_code + app_settings

Revision ID: 0021
Revises: 0020
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("kind", sa.String(16), server_default="full", nullable=False))
    op.create_table(
        "user_mfa",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("totp_secret_enc", sa.LargeBinary(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_step", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "user_recovery_code",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("code_hash", sa.String(), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_table("user_recovery_code")
    op.drop_table("user_mfa")
    op.drop_column("sessions", "kind")
```

Verify columns match the ORM (`TimestampMixin` provides `created_at`/`updated_at`; `UUIDPKMixin` provides `id`). If the mixin column names/types differ, align the migration to them (read `app/models/base.py`).

- [ ] **Step 6: Commit**

```bash
cd backend && .venv/bin/ruff check app/
git add app/models/ tests/test_mfa_models.py alembic/versions/0021_mfa.py
git commit -m "feat(mfa): models (user_mfa, recovery codes, app_settings) + session.kind + migration"
```

---

### Task 3: MFA service — TOTP + recovery codes

**Files:** Create `backend/app/services/mfa.py`; Test `backend/tests/test_mfa_service.py`

- [ ] **Step 1: Write the failing tests** — `backend/tests/test_mfa_service.py`

```python
import pyotp
import pytest

from app.services import mfa


def test_new_secret_and_uri():
    secret = mfa.new_secret()
    assert isinstance(secret, str) and len(secret) >= 16
    uri = mfa.provisioning_uri(secret, "user@x.io")
    assert uri.startswith("otpauth://totp/") and "OPNGMS" in uri


def test_verify_totp_accepts_current_and_rejects_replay():
    secret = mfa.new_secret()
    code = pyotp.TOTP(secret).now()
    ok, step = mfa.verify_totp(secret, code, last_used_step=None)
    assert ok and step is not None
    # same step replayed -> rejected
    ok2, _ = mfa.verify_totp(secret, code, last_used_step=step)
    assert not ok2


def test_verify_totp_rejects_wrong_code():
    secret = mfa.new_secret()
    ok, _ = mfa.verify_totp(secret, "000000", last_used_step=None)
    assert not ok


def test_recovery_codes_generate_hash_and_verify_once():
    codes, hashes = mfa.generate_recovery_codes(n=10)
    assert len(codes) == 10 and len(hashes) == 10
    # a clear code verifies against exactly its hash
    idx = mfa.find_recovery_match(codes[3], hashes)
    assert idx == 3
    assert mfa.find_recovery_match("not-a-code", hashes) is None
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** — `backend/app/services/mfa.py`:

```python
"""TOTP + recovery-code primitives for login MFA.

Pure functions over secrets/codes; persistence + encryption live in the API layer. The TOTP secret
is stored encrypted (MASTER_KEY) by the caller; recovery codes are argon2-hashed (one-time use)."""
import secrets

import pyotp

from app.core.security import hash_password, verify_password

ISSUER = "OPNGMS"
_TOTP_PERIOD = 30


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, account: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=ISSUER)


def verify_totp(secret: str, code: str, *, last_used_step: int | None) -> tuple[bool, int | None]:
    """Verify a 6-digit code with ±1 step skew + anti-replay. Returns (ok, accepted_step)."""
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit():
        return False, None
    totp = pyotp.TOTP(secret)
    import time
    now = int(time.time())
    for offset in (0, -1, 1):
        step = now // _TOTP_PERIOD + offset
        if secrets.compare_digest(totp.at(step * _TOTP_PERIOD), code):
            if last_used_step is not None and step <= last_used_step:
                return False, None
            return True, step
    return False, None


def _code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # crockford-ish, no ambiguous chars
    raw = "".join(secrets.choice(alphabet) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


def generate_recovery_codes(n: int = 10) -> tuple[list[str], list[str]]:
    """Return (clear_codes, hashes). Store hashes; show clear codes to the user ONCE."""
    codes = [_code() for _ in range(n)]
    hashes = [hash_password(c) for c in codes]
    return codes, hashes


def find_recovery_match(code: str, hashes: list[str]) -> int | None:
    """Index of the hash matching `code`, or None. Caller marks that code used."""
    code = (code or "").strip().upper()
    for i, h in enumerate(hashes):
        if verify_password(code, h):
            return i
    return None
```

Note `verify_totp` uses `time` (allowed; only `Date`/`Math.random` are blocked in JS workflow scripts, not Python). `verify_password`/`hash_password` are argon2.

- [ ] **Step 4: Run → pass.**

- [ ] **Step 5: Commit**

```bash
cd backend && .venv/bin/ruff check app/services/mfa.py
git add app/services/mfa.py tests/test_mfa_service.py
git commit -m "feat(mfa): TOTP verify (skew+anti-replay) + recovery-code primitives"
```

---

### Task 4: `app_settings` helper + MFA policy

**Files:** Create `backend/app/services/app_settings.py`; Test `backend/tests/test_app_settings.py`

- [ ] **Step 1: Failing test** — `backend/tests/test_app_settings.py`

```python
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.app_settings import get_mfa_policy, set_mfa_policy


async def test_mfa_policy_defaults_off_and_roundtrips(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        assert await get_mfa_policy(s) == "off"
        await set_mfa_policy(s, "privileged")
        await s.commit()
    async with factory() as s:
        assert await get_mfa_policy(s) == "privileged"
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** — `backend/app/services/app_settings.py`:

```python
"""Global key/value app settings (non-tenant). Currently: the MFA enforcement policy."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting

_MFA_KEY = "mfa_required"
MFA_MODES = {"off", "all", "privileged"}


async def get_mfa_policy(session: AsyncSession) -> str:
    row = (await session.execute(select(AppSetting).where(AppSetting.key == _MFA_KEY))).scalar_one_or_none()
    mode = (row.value or {}).get("mode") if row else None
    return mode if mode in MFA_MODES else "off"


async def set_mfa_policy(session: AsyncSession, mode: str) -> None:
    if mode not in MFA_MODES:
        raise ValueError(f"invalid mfa policy: {mode!r}")
    row = (await session.execute(select(AppSetting).where(AppSetting.key == _MFA_KEY))).scalar_one_or_none()
    if row is None:
        session.add(AppSetting(key=_MFA_KEY, value={"mode": mode}))
    else:
        row.value = {"mode": mode}
```

- [ ] **Step 4: Run → pass.** (`db_engine` is the conftest fixture; the test is DB-backed.)

- [ ] **Step 5: Commit**

```bash
cd backend
git add app/services/app_settings.py tests/test_app_settings.py
git commit -m "feat(mfa): global app_settings store + mfa policy get/set"
```

---

### Task 5: Session `kind` plumbing + enrollment auth dependency

**Files:** Modify `backend/app/services/auth.py`, `backend/app/core/deps.py`; Test `backend/tests/test_mfa_deps.py`

- [ ] **Step 1: Failing test** — `backend/tests/test_mfa_deps.py`

```python
import pytest
from fastapi import HTTPException

from app.core import deps
from app.models.session import Session


class _Svc:
    def __init__(self, sess, user): self._s, self._u = sess, user
    async def get_session_for_token(self, raw): return self._s
    async def get_user_for_session(self, sess): return self._u


async def test_get_current_user_rejects_non_full(monkeypatch):
    sess = Session(kind="mfa_setup")
    monkeypatch.setattr(deps, "AuthService", lambda s: _Svc(sess, object()))
    with pytest.raises(HTTPException) as ei:
        await deps.get_current_user(sess=sess, session=None)
    assert ei.value.status_code == 403


async def test_get_current_user_allows_full():
    sess = Session(kind="full")
    user = object()
    # call the inner resolver directly via the service shim
    import app.core.deps as d
    async def _stub(self): return user
    # get_current_user takes a resolved session + db session; patch the lookup
    d.AuthService = lambda s: _Svc(sess, user)
    out = await d.get_current_user(sess=sess, session=None)
    assert out is user
```

(If `get_current_user`'s exact signature differs after the edit, align the test to it — the assertion that matters: `mfa_setup`/`mfa_pending` sessions are rejected by `get_current_user`, `full` is accepted.)

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement.**

In `backend/app/services/auth.py`, give `create_session` a `kind` param and pass it to the `Session(...)`:
```python
    async def create_session(
        self, user: User, *, ttl_hours: int, kind: str = "full",
        ip: str | None = None, user_agent: str | None = None,
    ) -> tuple[Session, str]:
        ...
        sess = Session(
            user_id=user.id, kind=kind, token_hash=_hash_token(raw_token),
            csrf_token=secrets.token_urlsafe(32), last_seen_at=now,
            expires_at=now + timedelta(hours=ttl_hours), ip=ip,
            user_agent=(user_agent[:512] if user_agent else None),
        )
        ...
```

In `backend/app/core/deps.py`:
- Make `get_current_user` reject non-`full` sessions:
```python
async def get_current_user(
    sess: Session = Depends(get_current_session),
    session: AsyncSession = Depends(get_session),
) -> User:
    if sess.kind != "full":
        detail = "mfa_setup_required" if sess.kind == "mfa_setup" else "mfa_required"
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    user = await AuthService(session).get_user_for_session(sess)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    return user
```
- Add an enrollment dependency that ALSO accepts `mfa_setup` (for the enrollment endpoints + GET /api/me):
```python
async def get_enrollment_ctx(
    sess: Session = Depends(get_current_session),
    session: AsyncSession = Depends(get_session),
) -> tuple[User, Session]:
    """User for an endpoint reachable in MFA-setup mode (kind full or mfa_setup, NOT mfa_pending)."""
    if sess.kind not in ("full", "mfa_setup"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user = await AuthService(session).get_user_for_session(sess)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    return user, sess
```

- [ ] **Step 4: Run → pass; plus the existing auth/session suites to confirm no regression:**

```bash
cd backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest tests/test_mfa_deps.py tests/ -k "auth or session" -q
```

- [ ] **Step 5: Commit**

```bash
cd backend && .venv/bin/ruff check app/
git add app/services/auth.py app/core/deps.py tests/test_mfa_deps.py
git commit -m "feat(mfa): session.kind plumbing + enrollment-aware auth dependencies"
```

---

### Task 6: Enrollment API — `/api/me/mfa/*`

**Files:** Create `backend/app/api/mfa.py`, `backend/app/schemas/mfa.py`; Modify `backend/app/main.py` (include router), `backend/app/api/auth.py` (GET /api/me → expose setup flag); Test `backend/tests/test_mfa_enroll_api.py`

- [ ] **Step 1: Schemas** — `backend/app/schemas/mfa.py`:

```python
from pydantic import BaseModel


class PasswordIn(BaseModel):
    password: str


class CodeIn(BaseModel):
    code: str


class SetupOut(BaseModel):
    otpauth_uri: str
    secret: str


class RecoveryOut(BaseModel):
    recovery_codes: list[str]


class MfaStatusOut(BaseModel):
    enabled: bool
    recovery_codes_remaining: int


class MfaPolicyOut(BaseModel):
    mode: str


class MfaPolicyIn(BaseModel):
    mode: str
```

- [ ] **Step 2: Failing test** — `backend/tests/test_mfa_enroll_api.py` (mirror `tests/test_settings_api.py` for the app_client + login helpers). Cover: setup requires the right password; confirm enables + returns 10 recovery codes; status reflects enabled; disable requires password. Use `pyotp` to compute codes.

```python
import pyotp
from tests.factories import make_user  # adjust to the repo's factory for a password-set user

async def _login(api_client, email, pw="pw12345"):
    await api_client.post("/api/login", json={"email": email, "password": pw})

async def test_enroll_confirm_and_status(api_client, db_engine):
    # seed a normal user with a known password via /api/setup or a factory, then login
    # (use the same seeding approach as the existing auth API tests)
    ...  # seed user "u@x.io"/"pw12345"
    await _login(api_client, "u@x.io")
    csrf = api_client.cookies.get("opngms_csrf")
    H = {"X-OPNGMS-CSRF": csrf}
    r = await api_client.post("/api/me/mfa/setup", json={"password": "pw12345"}, headers=H)
    assert r.status_code == 200 and r.json()["secret"]
    secret = r.json()["secret"]
    code = pyotp.TOTP(secret).now()
    r2 = await api_client.post("/api/me/mfa/confirm", json={"code": code}, headers=H)
    assert r2.status_code == 200 and len(r2.json()["recovery_codes"]) == 10
    r3 = await api_client.get("/api/me/mfa")
    assert r3.json()["enabled"] is True and r3.json()["recovery_codes_remaining"] == 10

async def test_setup_rejects_wrong_password(api_client, db_engine):
    ...  # seed + login
    csrf = api_client.cookies.get("opngms_csrf")
    r = await api_client.post("/api/me/mfa/setup", json={"password": "WRONG"}, headers={"X-OPNGMS-CSRF": csrf})
    assert r.status_code == 403
```

(Match the repo's actual test client fixture + user-seeding helper used in `tests/test_*_api.py`; the exact fixture names there are authoritative.)

- [ ] **Step 3: Run → fail.**

- [ ] **Step 4: Implement** — `backend/app/api/mfa.py`:

```python
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.db import get_session
from app.core.deps import enforce_csrf, get_current_user, get_enrollment_ctx
from app.core.rbac import Action
from app.core.security import verify_password
from app.models.user import User
from app.models.user_mfa import UserMfa
from app.models.user_recovery_code import UserRecoveryCode
from app.schemas.mfa import CodeIn, MfaStatusOut, PasswordIn, RecoveryOut, SetupOut
from app.services import mfa as mfa_svc
from app.services.app_settings import MFA_MODES, get_mfa_policy, set_mfa_policy
from app.services.audit import AuditService

router = APIRouter(prefix="/api", tags=["mfa"])


async def _require_password(session, user: User, password: str) -> None:
    if not verify_password(password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Password required")


async def _mfa_row(session, user_id) -> UserMfa | None:
    return await session.get(UserMfa, user_id)


@router.get("/me/mfa", response_model=MfaStatusOut)
async def mfa_status(ctx=Depends(get_enrollment_ctx), session: AsyncSession = Depends(get_session)) -> MfaStatusOut:
    user, _ = ctx
    row = await _mfa_row(session, user.id)
    remaining = (await session.execute(
        select(func.count()).select_from(UserRecoveryCode).where(
            UserRecoveryCode.user_id == user.id, UserRecoveryCode.used_at.is_(None)))).scalar() or 0
    return MfaStatusOut(enabled=bool(row and row.enabled), recovery_codes_remaining=int(remaining))


@router.post("/me/mfa/setup", response_model=SetupOut, dependencies=[Depends(enforce_csrf)])
async def mfa_setup(body: PasswordIn, ctx=Depends(get_enrollment_ctx),
                    session: AsyncSession = Depends(get_session)) -> SetupOut:
    user, _ = ctx
    await _require_password(session, user, body.password)
    secret = mfa_svc.new_secret()
    row = await _mfa_row(session, user.id)
    if row is None:
        row = UserMfa(user_id=user.id)
        session.add(row)
    row.enabled = False
    row.totp_secret_enc = crypto.encrypt(secret)
    row.confirmed_at = None
    row.last_used_step = None
    await session.commit()
    return SetupOut(otpauth_uri=mfa_svc.provisioning_uri(secret, user.email), secret=secret)


@router.post("/me/mfa/confirm", response_model=RecoveryOut, dependencies=[Depends(enforce_csrf)])
async def mfa_confirm(body: CodeIn, ctx=Depends(get_enrollment_ctx),
                      session: AsyncSession = Depends(get_session)) -> RecoveryOut:
    user, sess = ctx
    row = await _mfa_row(session, user.id)
    if row is None or not row.totp_secret_enc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No pending enrollment")
    secret = crypto.decrypt(row.totp_secret_enc)
    ok, step = mfa_svc.verify_totp(secret, body.code, last_used_step=row.last_used_step)
    if not ok:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Invalid code")
    row.enabled = True
    row.confirmed_at = datetime.now(UTC)
    row.last_used_step = step
    # fresh recovery codes
    await session.execute(UserRecoveryCode.__table__.delete().where(UserRecoveryCode.user_id == user.id))
    codes, hashes = mfa_svc.generate_recovery_codes(10)
    for h in hashes:
        session.add(UserRecoveryCode(user_id=user.id, code_hash=h))
    # if this session was setup-only, upgrade it to full now that MFA is enrolled
    if sess.kind == "mfa_setup":
        sess.kind = "full"
    await AuditService(session).record(actor_user_id=user.id, tenant_id=None, action="mfa.confirm",
                                       target_type="user", target_id=str(user.id), ip=None, details={})
    await session.commit()
    return RecoveryOut(recovery_codes=codes)


@router.post("/me/mfa/disable", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(enforce_csrf)])
async def mfa_disable(body: PasswordIn, user: User = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session)) -> None:
    await _require_password(session, user, body.password)
    await session.execute(UserRecoveryCode.__table__.delete().where(UserRecoveryCode.user_id == user.id))
    row = await _mfa_row(session, user.id)
    if row is not None:
        await session.delete(row)
    await AuditService(session).record(actor_user_id=user.id, tenant_id=None, action="mfa.disable",
                                       target_type="user", target_id=str(user.id), ip=None, details={})
    await session.commit()


@router.post("/me/mfa/recovery/regenerate", response_model=RecoveryOut, dependencies=[Depends(enforce_csrf)])
async def mfa_regen(body: PasswordIn, user: User = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)) -> RecoveryOut:
    await _require_password(session, user, body.password)
    row = await _mfa_row(session, user.id)
    if row is None or not row.enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA not enabled")
    await session.execute(UserRecoveryCode.__table__.delete().where(UserRecoveryCode.user_id == user.id))
    codes, hashes = mfa_svc.generate_recovery_codes(10)
    for h in hashes:
        session.add(UserRecoveryCode(user_id=user.id, code_hash=h))
    await AuditService(session).record(actor_user_id=user.id, tenant_id=None, action="mfa.recovery_regenerate",
                                       target_type="user", target_id=str(user.id), ip=None, details={})
    await session.commit()
    return RecoveryOut(recovery_codes=codes)
```

(Confirm the `AuditService.record` signature against `app/services/audit.py` and match it.)

- [ ] **Step 5: GET /api/me exposes the setup flag** — in `backend/app/api/auth.py`, change the `/me` route to use `get_enrollment_ctx` and add `mfa_setup_required` to `MeOut`:

In `app/schemas/auth.py`, add to `MeOut`: `mfa_setup_required: bool = False`.
In `app/api/auth.py`:
```python
@router.get("/me", response_model=MeOut)
async def me(ctx=Depends(get_enrollment_ctx)) -> MeOut:
    user, sess = ctx
    return MeOut(id=user.id, email=user.email, name=user.name,
                 is_superadmin=user.is_superadmin, mfa_setup_required=(sess.kind == "mfa_setup"))
```
(Import `get_enrollment_ctx`. Keep the existing behavior for full sessions: `mfa_setup_required=False`.)

- [ ] **Step 6: Register the router** in `backend/app/main.py` (import `from app.api.mfa import router as mfa_router` and `app.include_router(mfa_router)`).

- [ ] **Step 7: Run → pass.**

- [ ] **Step 8: Commit**

```bash
cd backend && .venv/bin/ruff check app/
git add app/api/mfa.py app/schemas/mfa.py app/schemas/auth.py app/api/auth.py app/main.py tests/test_mfa_enroll_api.py
git commit -m "feat(mfa): enrollment endpoints (/api/me/mfa/*) + me setup flag"
```

---

### Task 7: Two-step login — `/api/login` branch + `/api/login/mfa`

**Files:** Modify `backend/app/api/auth.py`, `backend/app/schemas/auth.py`; Test `backend/tests/test_mfa_login_api.py`

- [ ] **Step 1: Schemas** — in `app/schemas/auth.py` add:
```python
class LoginOut(BaseModel):
    status: str           # "ok" | "mfa_required" | "mfa_setup_required"
    user: MeOut | None = None
```

- [ ] **Step 2: Failing test** — `backend/tests/test_mfa_login_api.py`: a user with MFA enabled → `POST /api/login` returns `{status:"mfa_required"}` and NO full session (a protected call 403/401); `POST /api/login/mfa` with a valid TOTP → `{status:"ok"}` + protected call works; a recovery code also completes login and is single-use; wrong code → 401. (Seed a user, enroll MFA via the Task 6 endpoints or directly insert `user_mfa` with a known secret.)

- [ ] **Step 3: Implement.** Change the **success branch** of `login` in `app/api/auth.py` (after `user` is authenticated and the old cookie dropped):

```python
    from app.models.user_mfa import UserMfa
    from app.services.app_settings import get_mfa_policy

    mfa_row = await session.get(UserMfa, user.id)
    policy = await get_mfa_policy(session)
    is_priv = user.is_superadmin  # (privileged = superadmin or tenant_admin; membership check optional for MVP)

    if mfa_row and mfa_row.enabled:
        kind = "mfa_pending"
    elif policy == "all" or (policy == "privileged" and is_priv):
        kind = "mfa_setup"
    else:
        kind = "full"

    sess, raw_token = await svc.create_session(
        user, ttl_hours=(1 if kind != "full" else settings.session_ttl_hours), kind=kind,
        ip=client_ip, user_agent=request.headers.get("user-agent"))
    try:
        login_limiter.reset(key)
    except Exception:  # noqa: BLE001
        logger.error("login rate-limiter reset failed", exc_info=True)
    await AuditService(session).record(actor_user_id=user.id, tenant_id=None,
        action=("auth.login" if kind == "full" else f"auth.login.{kind}"),
        target_type="session", target_id=str(sess.id), ip=client_ip, details={})
    await session.commit()
    max_age = (3600 if kind != "full" else settings.session_ttl_hours * 3600)
    response.set_cookie(SESSION_COOKIE, raw_token, httponly=True, secure=True, samesite="lax", max_age=max_age)
    response.set_cookie(CSRF_COOKIE, sess.csrf_token, httponly=False, secure=True, samesite="lax", max_age=max_age)
    if kind == "mfa_pending":
        return LoginOut(status="mfa_required")
    if kind == "mfa_setup":
        return LoginOut(status="mfa_setup_required",
                        user=MeOut(id=user.id, email=user.email, name=user.name,
                                   is_superadmin=user.is_superadmin, mfa_setup_required=True))
    return LoginOut(status="ok", user=MeOut(id=user.id, email=user.email, name=user.name,
                                            is_superadmin=user.is_superadmin))
```
Change the route decorator `response_model=MeOut` → `response_model=LoginOut`.

Add the second-step endpoint:
```python
@router.post("/login/mfa", response_model=LoginOut, dependencies=[Depends(enforce_csrf)])
async def login_mfa(body: CodeIn, request: Request, response: Response,
                    sess: Session = Depends(get_current_session),
                    session: AsyncSession = Depends(get_session)) -> LoginOut:
    if sess.kind != "mfa_pending":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No MFA challenge")
    client_ip = _client_ip(request)
    key = f"mfa|{sess.user_id}|{client_ip or '?'}"
    allowed, retry = login_limiter.check(key)
    if not allowed:
        raise HTTPException(status_code=429, detail="Too many attempts", headers={"Retry-After": str(retry)})
    user = await AuthService(session).get_user_for_session(sess)
    row = await session.get(UserMfa, sess.user_id)
    if user is None or row is None or not row.enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No MFA challenge")
    secret = crypto.decrypt(row.totp_secret_enc)
    ok, step = mfa_svc.verify_totp(secret, body.code, last_used_step=row.last_used_step)
    used_recovery = False
    if ok:
        row.last_used_step = step
    else:
        # recovery-code fallback
        codes = (await session.execute(select(UserRecoveryCode).where(
            UserRecoveryCode.user_id == user.id, UserRecoveryCode.used_at.is_(None)))).scalars().all()
        idx = mfa_svc.find_recovery_match(body.code, [c.code_hash for c in codes])
        if idx is not None:
            codes[idx].used_at = datetime.now(UTC); used_recovery = True
    if not ok and not used_recovery:
        login_limiter.record_failure(key)
        await AuditService(session).record(actor_user_id=user.id, tenant_id=None, action="mfa.login_failed",
                                           target_type="session", target_id=str(sess.id), ip=client_ip, details={})
        await session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid code")
    # upgrade: drop the pending session, mint a full one
    raw_old = request.cookies.get(SESSION_COOKIE)
    if raw_old:
        await AuthService(session).delete_session_by_token(raw_old)
    settings = get_settings()
    full, raw_token = await AuthService(session).create_session(
        user, ttl_hours=settings.session_ttl_hours, kind="full",
        ip=client_ip, user_agent=request.headers.get("user-agent"))
    login_limiter.reset(key)
    await AuditService(session).record(actor_user_id=user.id, tenant_id=None,
        action=("mfa.recovery_used" if used_recovery else "mfa.login_success"),
        target_type="session", target_id=str(full.id), ip=client_ip, details={})
    await session.commit()
    max_age = settings.session_ttl_hours * 3600
    response.set_cookie(SESSION_COOKIE, raw_token, httponly=True, secure=True, samesite="lax", max_age=max_age)
    response.set_cookie(CSRF_COOKIE, full.csrf_token, httponly=False, secure=True, samesite="lax", max_age=max_age)
    return LoginOut(status="ok", user=MeOut(id=user.id, email=user.email, name=user.name, is_superadmin=user.is_superadmin))
```
Add the needed imports at the top of `auth.py`: `from datetime import UTC, datetime`, `from sqlalchemy import select`, `from app.core import crypto`, `from app.core.deps import get_current_session`, `from app.models.user_mfa import UserMfa`, `from app.models.user_recovery_code import UserRecoveryCode`, `from app.services import mfa as mfa_svc`, `from app.schemas.auth import CodeIn, LoginOut` (CodeIn is in schemas/mfa — import from there, or re-export). Ensure isort order keeps `ruff check app/` clean.

- [ ] **Step 4: Run → pass; full auth suite.**

```bash
cd backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest tests/ -k "auth or mfa or session" -q
```

- [ ] **Step 5: Commit**

```bash
cd backend && .venv/bin/ruff check app/
git add app/api/auth.py app/schemas/auth.py tests/test_mfa_login_api.py
git commit -m "feat(mfa): two-step login (pending session) + /api/login/mfa (TOTP + recovery)"
```

---

### Task 8: Superadmin policy + admin reset

**Files:** Modify `backend/app/api/mfa.py`; Test `backend/tests/test_mfa_admin_api.py`

- [ ] **Step 1: Failing test** — `backend/tests/test_mfa_admin_api.py`: a superadmin GET/PUT `/api/admin/mfa-policy` (off→privileged); a non-superadmin gets 403; superadmin `POST /api/users/{id}/mfa/reset` clears a target's MFA; non-superadmin 403.

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** — append to `backend/app/api/mfa.py`:

```python
from app.core.deps import require_org
from app.schemas.mfa import MfaPolicyIn, MfaPolicyOut


@router.get("/admin/mfa-policy", response_model=MfaPolicyOut)
async def mfa_policy_get(user: User = Depends(require_org(Action.USER_MANAGE)),
                         session: AsyncSession = Depends(get_session)) -> MfaPolicyOut:
    return MfaPolicyOut(mode=await get_mfa_policy(session))


@router.put("/admin/mfa-policy", response_model=MfaPolicyOut, dependencies=[Depends(enforce_csrf)])
async def mfa_policy_set(body: MfaPolicyIn, user: User = Depends(require_org(Action.USER_MANAGE)),
                         session: AsyncSession = Depends(get_session)) -> MfaPolicyOut:
    if body.mode not in MFA_MODES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid mode")
    await set_mfa_policy(session, body.mode)
    await AuditService(session).record(actor_user_id=user.id, tenant_id=None, action="mfa.policy_change",
                                       target_type="app_settings", target_id="mfa_required", ip=None,
                                       details={"mode": body.mode})
    await session.commit()
    return MfaPolicyOut(mode=body.mode)


@router.post("/users/{user_id}/mfa/reset", status_code=status.HTTP_204_NO_CONTENT,
             dependencies=[Depends(enforce_csrf)])
async def mfa_admin_reset(user_id: uuid.UUID, actor: User = Depends(require_org(Action.USER_MANAGE)),
                          session: AsyncSession = Depends(get_session)) -> None:
    await session.execute(UserRecoveryCode.__table__.delete().where(UserRecoveryCode.user_id == user_id))
    row = await session.get(UserMfa, user_id)
    if row is not None:
        await session.delete(row)
    await AuditService(session).record(actor_user_id=actor.id, tenant_id=None, action="mfa.admin_reset",
                                       target_type="user", target_id=str(user_id), ip=None, details={})
    await session.commit()
```

Confirm `Action.USER_MANAGE` exists in `app/core/rbac.py` and is superadmin-gated (use the right action — whatever guards user administration; if there is a dedicated superadmin guard, use it). If no suitable Action, gate on `user.is_superadmin` directly via a small dependency.

- [ ] **Step 4: Run → pass; full backend suite + ruff.**

```bash
cd backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest -q && .venv/bin/ruff check app/
```

- [ ] **Step 5: Commit**

```bash
cd backend
git add app/api/mfa.py tests/test_mfa_admin_api.py
git commit -m "feat(mfa): superadmin policy endpoints + admin reset of a user's MFA"
```

---

### Task 9: Break-glass CLI

**Files:** Create `backend/app/cli.py`; Test `backend/tests/test_mfa_cli.py`

- [ ] **Step 1: Failing test** — `backend/tests/test_mfa_cli.py`: seed a user with MFA enabled; call the CLI reset function; assert the user_mfa row + recovery codes are gone. Test the underlying coroutine `reset_user_mfa(email)` directly (don't shell out).

```python
async def test_cli_reset_clears_mfa(db_engine, monkeypatch):
    # point the CLI at the test engine, seed a user + user_mfa + a recovery code, then:
    from app.cli import reset_user_mfa
    n = await reset_user_mfa("u@x.io", engine=db_engine)
    assert n == 1  # one user reset
    # assert user_mfa gone
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** — `backend/app/cli.py`:

```python
"""Host-level admin CLI (break-glass). Usage: python -m app.cli mfa-reset --email <email>.

Connects via ADMIN_DATABASE_URL (owner role) and clears a user's MFA + recovery codes. This is the
recovery path for the last superadmin locked out of the web UI."""
import argparse
import asyncio

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.config import get_settings
from app.core.db import make_engine
from app.models.user import User
from app.models.user_mfa import UserMfa
from app.models.user_recovery_code import UserRecoveryCode


async def reset_user_mfa(email: str, *, engine: AsyncEngine | None = None) -> int:
    eng = engine or make_engine(get_settings().admin_database_url)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    async with factory() as s:
        user = (await s.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            return 0
        await s.execute(delete(UserRecoveryCode).where(UserRecoveryCode.user_id == user.id))
        await s.execute(delete(UserMfa).where(UserMfa.user_id == user.id))
        await s.commit()
    if engine is None:
        await eng.dispose()
    return 1


def main() -> None:
    p = argparse.ArgumentParser(prog="app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("mfa-reset")
    r.add_argument("--email", required=True)
    args = p.parse_args()
    if args.cmd == "mfa-reset":
        n = asyncio.run(reset_user_mfa(args.email))
        print(f"MFA reset for {args.email}: {n} user(s) affected")


if __name__ == "__main__":
    main()
```

Confirm `get_settings().admin_database_url` is the correct attribute name (read `app/core/config.py`). Adjust if it differs.

- [ ] **Step 4: Run → pass.**

- [ ] **Step 5: Commit**

```bash
cd backend && .venv/bin/ruff check app/
git add app/cli.py tests/test_mfa_cli.py
git commit -m "feat(mfa): break-glass CLI (python -m app.cli mfa-reset --email)"
```

**→ Open PR 1 (backend), wait for green CI, merge. Then continue the frontend on a fresh branch off main.**

---

## FRONTEND (→ PR 2)

> After PR 1 merges, branch off main and run `npm run gen:api` first so the new endpoints/types are available. Mirror the existing harnesses: `src/pages/__tests__/*` for page tests, `src/test/server.ts` MSW, `renderWithProviders`/`withTenant`. Keep all `data-testid`s stable.

### Task 10: Login MFA step + auth types

**Files:** Modify `frontend/src/pages/LoginPage.tsx`, `frontend/src/auth/AuthProvider.tsx`; Test `frontend/src/pages/__tests__/loginMfa.test.tsx`

- [ ] Login now posts to `/api/login` and branches on `data.status`: `ok` → `setMe(user)`; `mfa_required` → render a 6-digit `code` input (testid `mfa-code`) + a "use a recovery code" toggle, posting `{code}` to `/api/login/mfa` (with the CSRF header from the cookie); on `{status:"ok"}` → `setMe`; `mfa_setup_required` → `setMe(user)` (the `mfa_setup_required` flag routes to the gate, Task 12). Add a failing RTL test driving: password → `mfa_required` → enter code → success. Commit `feat(mfa): login MFA code step`.

### Task 11: Enrollment wizard (Account → Security)

**Files:** Create `frontend/src/security/MfaPanel.tsx` + a small `QrCode` component; add a nav entry/section; Test `frontend/src/security/__tests__/mfaPanel.test.tsx`

- [ ] A panel showing MFA status (`GET /api/me/mfa`); an **Enroll** flow: password → `POST /api/me/mfa/setup` → render the QR from `otpauth_uri` (use a tiny dependency-free SVG QR component, or add `qrcode` and render to a data-uri) + show the secret → enter code → `POST /api/me/mfa/confirm` → show the 10 recovery codes once (copy/download) ; **Regenerate codes** (password) ; **Disable** (password). Testids `mfa-enroll`, `mfa-secret`, `mfa-confirm-code`, `mfa-recovery-codes`, `mfa-disable`. Failing test → implement → commit `feat(mfa): enrollment panel + QR + recovery codes`.

### Task 12: Forced setup gate

**Files:** Modify `frontend/src/auth/ProtectedRoute.tsx` (or `AppShell`); Test update

- [ ] When `me.mfa_setup_required` is true, render a full-screen **MFA setup gate** (reusing the enrollment flow) instead of the app, until enrollment completes (after which `GET /api/me` returns `mfa_setup_required:false`). Commit `feat(mfa): forced enrollment gate when policy requires`.

### Task 13: Superadmin policy control + per-user reset

**Files:** a settings/admin control for `GET/PUT /api/admin/mfa-policy` (off/all/privileged); a **Reset MFA** action on the users list calling `POST /api/users/{id}/mfa/reset`; tests.

- [ ] Failing tests → implement → commit `feat(mfa): superadmin policy control + per-user MFA reset`.

### Task 14: i18n, regen, full suite

- [ ] Add `auth.mfa.*` i18n; `npm run gen:api`; `npx vitest run && npm run lint && npx tsc --noEmit` green; commit `chore(mfa): i18n + regen client types`.

---

## Task 15: Docs + live verify

- [ ] README: add an **MFA** subsection under Security & multi-tenancy (TOTP + recovery codes, the policy modes, the admin reset, and the **break-glass** `python -m app.cli mfa-reset --email <e>`); update the Roadmap row.
- [ ] **Live verify** against the running stack: enroll with a real authenticator app, complete two-step login, log in with a recovery code (confirm single-use), flip the policy to `all` and confirm the setup gate forces a fresh user to enroll, superadmin-reset another user, and run the CLI break-glass.
- [ ] Final suites green (backend `pytest -q` + `ruff check app/`; frontend `vitest run` + `lint` + `tsc`).

---

## Self-Review notes
- **Spec coverage:** models+migration (T2) · TOTP/recovery primitives (T3) · policy store (T4) · session.kind + enrollment deps (T5) · enrollment API (T6) · two-step login (T7) · policy + admin reset (T8) · CLI break-glass (T9) · frontend login/enroll/gate/policy/reset (T10–14) · docs + live verify (T15). Every spec section maps to a task.
- **Security invariants:** secret encrypted at rest (T6 `crypto.encrypt`); recovery codes argon2-hashed + one-time (`used_at`); anti-replay via `last_used_step` (T3/T7); MFA-step rate-limited (T7); pending/setup sessions cannot reach app endpoints (T5 `get_current_user` rejects non-`full`); password re-auth on MFA mutations (T6); full audit trail; admin reset + CLI break-glass gated (superadmin / host).
- **Type consistency:** `Session.kind ∈ {full, mfa_pending, mfa_setup}`; `LoginOut.status ∈ {ok, mfa_required, mfa_setup_required}`; `create_session(..., kind=...)`; `verify_totp(secret, code, last_used_step=) -> (ok, step)`; `MfaStatusOut{enabled, recovery_codes_remaining}` used consistently across tasks.
- **Codebase confirmations to do at execution time (flagged in-task):** `AuditService.record` signature; `get_settings().admin_database_url`; `Action.USER_MANAGE` (or a superadmin guard); `TimestampMixin`/`UUIDPKMixin` column shapes for the migration; the test client + user-seeding fixtures used by the existing `tests/test_*_api.py`.
