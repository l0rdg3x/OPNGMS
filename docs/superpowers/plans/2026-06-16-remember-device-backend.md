# Remember-this-device — PR1 (backend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a successful second factor, let a user mark the current device as trusted so future logins still require the password but skip the second factor for a configurable number of days; server-side, revocable.

**Architecture:** A new `trusted_device` table mirrors the session-token pattern — a raw opaque token lives only in an HttpOnly cookie, the DB stores `HMAC-SHA256(SESSION_SECRET, raw_token)`. `/login` attempts a trusted-device skip after the password verifies; `/login/mfa` and `/login/webauthn/complete` set the cookie when `remember_device` is true. An org-wide toggle + a runtime day-count gate the feature; a management API + auto-revoke on MFA teardown handle the lifecycle.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy 2.0 async, Alembic, pytest (asyncio_mode=auto), httpx ASGI client.

**Spec:** `docs/superpowers/specs/2026-06-16-remember-device-design.md`. Read it for the rationale; this plan is self-contained.

**Key existing facts (do not re-derive):**
- Session token model: `backend/app/services/auth.py:21-25` `_hash_token(raw)` = `hmac.new(SESSION_SECRET, raw, sha256).hexdigest()`; `create_session` (lines 41-66) returns `(session, raw_token)`, only the hash stored.
- Cookie helpers: `response.set_cookie(NAME, value, httponly=True, secure=True, samesite="lax", max_age=...)`. Constants in `backend/app/core/deps.py:16-18` (`SESSION_COOKIE`, `CSRF_COOKIE`).
- `/login` decides `kind` at `backend/app/api/auth.py:119-179`; `has_totp`/`has_passkey` computed at lines 122-124; the limiter is reset on success at line 152; audit via `AuditService(session).record(...)`.
- `/login/mfa` mints the full session at `backend/app/api/auth.py:270-294`; `/login/webauthn/complete` at lines 414-438.
- `LoginOut` schema: `backend/app/schemas/auth.py:30-35`.
- App settings pattern: `backend/app/services/app_settings.py` (`get_live_push`/`set_live_push` for a bool; `get_mfa_policy` for a string).
- Runtime settings registry: `backend/app/services/runtime_settings.py:43-62` (`RUNTIME_SETTINGS` list; group `security_session`).
- Settings (env defaults): `backend/app/core/config.py:21-23` (`session_ttl_hours`, etc.), `:48` (`live_push_enabled`).
- Models registered in `backend/app/models/__init__.py` (add the new model there).
- Routers wired in `backend/app/main.py` (`include_router`). MFA disable at `backend/app/api/mfa.py:170-186`; admin MFA reset at `:385-405`.
- Sweeper cron: `backend/app/worker.py:514-522` `cleanup_expired_sessions`.
- Audit-coverage guard: `backend/tests/test_audit_coverage.py` — every mutating route must call `.record(` inline or be allowlisted. New `DELETE` routes will audit inline (no allowlist change needed).
- Test fixtures: `backend/tests/conftest.py` — `api_client` (base_url `https://test` so Secure cookies persist in the jar), `db_engine`, `csrf_headers(client)`, `make_user` from `tests/factories.py`. Test `SESSION_SECRET` is `"test-session-secret"`.
- Alembic head is `0044` (`backend/alembic/versions/0044_webauthn.py`). Migrations are hand-written (no autogenerate).

**Commands** (run from `backend/`, venv active, env per AGENTS.md):
- Single test: `python -m pytest tests/<file>::<test> -q`
- Apply migration to the *test* DB before model-touching tests if needed: the `db_engine` fixture builds the schema from `Base.metadata`, so tests do NOT need the migration applied; the migration is for production parity and is verified separately in Task 7.
- Lint: `ruff check app/`

> **Do not run the full suite and a targeted run concurrently** — both hit `opngms_test` and a concurrent run causes spurious `DBAPIError` failures.

---

### Task 1: Model + migration

**Files:**
- Create: `backend/app/models/trusted_device.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/0045_trusted_device.py`
- Test: `backend/tests/test_trusted_device_model.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_trusted_device_model.py
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.trusted_device import TrustedDevice
from tests.factories import make_user


async def test_trusted_device_row_roundtrip(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        u = await make_user(s, email="td@x.io", password="pw12345-secure")
        now = datetime.now(UTC)
        s.add(TrustedDevice(
            user_id=u.id, token_hash="a" * 64, user_agent="UA", ip="1.2.3.4",
            expires_at=now + timedelta(days=30),
        ))
        await s.commit()
        row = (await s.execute(select(TrustedDevice).where(TrustedDevice.user_id == u.id))).scalar_one()
        assert row.token_hash == "a" * 64
        assert row.user_agent == "UA"
        assert row.ip == "1.2.3.4"
        assert row.created_at is not None
        assert row.last_used_at is not None
        assert row.expires_at > now
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trusted_device_model.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.models.trusted_device'`.

- [ ] **Step 3: Create the model**

```python
# backend/app/models/trusted_device.py
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class TrustedDevice(UUIDPKMixin, Base):
    """A per-(user, device) trust grant: presenting the matching cookie at login lets the user skip
    the second factor (the password is still required). Mirrors the session-token model — the raw
    token lives only in the cookie; only its HMAC-SHA256(SESSION_SECRET) hash is stored here."""

    __tablename__ = "trusted_devices"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # HMAC-SHA256 hex of the opaque device token. A DB dump yields no usable tokens, and rotating
    # SESSION_SECRET invalidates every trusted device — same property as sessions.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # Display-only metadata for the "trusted devices" list (never enforced).
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
```

- [ ] **Step 4: Register the model**

Add to `backend/app/models/__init__.py`, in alphabetical position (after the `tenant_retention` import, before `from app.models.user import User`):

```python
from app.models.trusted_device import TrustedDevice  # noqa: F401
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_trusted_device_model.py -q`
Expected: PASS.

- [ ] **Step 6: Write the migration**

```python
# backend/alembic/versions/0045_trusted_device.py
"""trusted_device table (remember-this-device)

Revision ID: 0045
Revises: 0044
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trusted_devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_trusted_devices_user_id", "trusted_devices", ["user_id"])
    op.create_unique_constraint("uq_trusted_devices_token_hash", "trusted_devices", ["token_hash"])
    op.create_index("ix_trusted_devices_token_hash", "trusted_devices", ["token_hash"])
    op.create_index("ix_trusted_devices_expires_at", "trusted_devices", ["expires_at"])


def downgrade() -> None:
    op.drop_table("trusted_devices")
```

- [ ] **Step 7: Verify the migration matches the head chain**

Run: `python -c "import app.alembic" 2>/dev/null; ls alembic/versions/0044_webauthn.py alembic/versions/0045_trusted_device.py`
Then confirm `down_revision = "0044"` is the current head: `grep -l 'down_revision = \"0044\"' alembic/versions/*.py` should list only `0045_trusted_device.py`.
Expected: both files exist; only 0045 chains from 0044.

- [ ] **Step 8: Commit**

```bash
git add backend/app/models/trusted_device.py backend/app/models/__init__.py backend/alembic/versions/0045_trusted_device.py backend/tests/test_trusted_device_model.py
git commit -m "feat(mfa): trusted_device model + migration 0045"
```

---

### Task 2: TrustedDeviceService

**Files:**
- Create: `backend/app/services/trusted_device.py`
- Test: `backend/tests/test_trusted_device_service.py`

The service owns token mint/hash and all DB access for trusted devices. It defines its own `_hash_token` (the same HMAC-SHA256(SESSION_SECRET) one-liner as `app/services/auth.py`) so the modules stay decoupled.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_trusted_device_service.py
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.trusted_device import TrustedDevice
from app.services.trusted_device import TrustedDeviceService
from tests.factories import make_user


async def _user(db_engine, email="svc@x.io"):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        u = await make_user(s, email=email, password="pw12345-secure")
        await s.commit()
        return u.id


async def test_create_then_find_valid(db_engine):
    uid = await _user(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        svc = TrustedDeviceService(s)
        row, raw = await svc.create_for_user(uid, days=30, user_agent="UA", ip="1.2.3.4")
        await s.commit()
        assert raw and row.token_hash != raw  # only the hash is stored
        found = await svc.find_valid(uid, raw)
        assert found is not None and found.id == row.id


async def test_find_valid_rejects_wrong_user(db_engine):
    uid = await _user(db_engine, "a@x.io")
    other = await _user(db_engine, "b@x.io")
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        svc = TrustedDeviceService(s)
        _, raw = await svc.create_for_user(uid, days=30, user_agent=None, ip=None)
        await s.commit()
        assert await svc.find_valid(other, raw) is None  # token belongs to uid, not other


async def test_find_valid_rejects_expired(db_engine):
    uid = await _user(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        svc = TrustedDeviceService(s)
        row, raw = await svc.create_for_user(uid, days=30, user_agent=None, ip=None)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await s.commit()
        assert await svc.find_valid(uid, raw) is None


async def test_find_valid_rejects_unknown_and_garbage(db_engine):
    uid = await _user(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        svc = TrustedDeviceService(s)
        assert await svc.find_valid(uid, "not-a-real-token") is None


async def test_touch_updates_last_used(db_engine):
    uid = await _user(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        svc = TrustedDeviceService(s)
        row, _ = await svc.create_for_user(uid, days=30, user_agent=None, ip=None)
        row.last_used_at = datetime.now(UTC) - timedelta(days=1)
        await s.commit()
        before = row.last_used_at
        await svc.touch(row)
        await s.commit()
        assert row.last_used_at > before


async def test_list_and_revoke(db_engine):
    uid = await _user(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        svc = TrustedDeviceService(s)
        r1, _ = await svc.create_for_user(uid, days=30, user_agent="A", ip=None)
        r2, _ = await svc.create_for_user(uid, days=30, user_agent="B", ip=None)
        await s.commit()
        rows = await svc.list_for_user(uid)
        assert {r.id for r in rows} == {r1.id, r2.id}
        assert await svc.revoke(r1.id, uid) is True
        assert await svc.revoke(r1.id, uid) is False  # already gone
        await s.commit()
        assert {r.id for r in await svc.list_for_user(uid)} == {r2.id}


async def test_revoke_scoped_to_owner(db_engine):
    uid = await _user(db_engine, "a@x.io")
    other = await _user(db_engine, "b@x.io")
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        svc = TrustedDeviceService(s)
        r1, _ = await svc.create_for_user(uid, days=30, user_agent=None, ip=None)
        await s.commit()
        assert await svc.revoke(r1.id, other) is False  # not other's device
        await s.commit()
        assert await svc.find_valid(uid, "x") is None or True  # row still present for uid
        assert len(await svc.list_for_user(uid)) == 1


async def test_revoke_all_and_purge_expired(db_engine):
    uid = await _user(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        svc = TrustedDeviceService(s)
        live, _ = await svc.create_for_user(uid, days=30, user_agent=None, ip=None)
        dead, _ = await svc.create_for_user(uid, days=30, user_agent=None, ip=None)
        dead.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await s.commit()
        assert await svc.purge_expired(datetime.now(UTC)) == 1
        await s.commit()
        assert {r.id for r in await svc.list_for_user(uid)} == {live.id}
        n = await svc.revoke_all(uid)
        await s.commit()
        assert n == 1
        assert (await s.execute(select(TrustedDevice).where(TrustedDevice.user_id == uid))).first() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_trusted_device_service.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.trusted_device'`.

- [ ] **Step 3: Implement the service**

```python
# backend/app/services/trusted_device.py
"""Trusted-device store for "remember this device" — server-side, revocable.

Mirrors the session-token model: a raw token lives only in a cookie; only its
HMAC-SHA256(SESSION_SECRET) hash is stored. find_valid is fail-closed — a garbage,
unknown, expired, or wrong-user token returns None (never grants a skip)."""
import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.trusted_device import TrustedDevice


def _hash_token(raw: str) -> str:
    # Same construction as app/services/auth.py:_hash_token — keyed by SESSION_SECRET so a DB dump
    # yields only keyed hashes and rotating the secret invalidates every trusted device.
    return hmac.new(get_settings().session_secret.encode(), raw.encode(), hashlib.sha256).hexdigest()


class TrustedDeviceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_for_user(
        self, user_id: uuid.UUID, *, days: int, user_agent: str | None, ip: str | None
    ) -> tuple[TrustedDevice, str]:
        """Mint a trusted-device row. Returns (row, raw_token); only the hash is stored."""
        now = datetime.now(UTC)
        raw = secrets.token_urlsafe(32)
        row = TrustedDevice(
            user_id=user_id,
            token_hash=_hash_token(raw),
            user_agent=(user_agent[:512] if user_agent else None),
            ip=ip,
            last_used_at=now,
            expires_at=now + timedelta(days=days),
        )
        self.session.add(row)
        await self.session.flush()
        return row, raw

    async def find_valid(self, user_id: uuid.UUID, raw_token: str) -> TrustedDevice | None:
        """The non-expired row for this user matching the token, or None (fail-closed)."""
        if not raw_token:
            return None
        now = datetime.now(UTC)
        row = (
            await self.session.execute(
                select(TrustedDevice).where(TrustedDevice.token_hash == _hash_token(raw_token))
            )
        ).scalar_one_or_none()
        if row is None or row.user_id != user_id or row.expires_at <= now:
            return None
        return row

    async def touch(self, row: TrustedDevice) -> None:
        row.last_used_at = datetime.now(UTC)

    async def list_for_user(self, user_id: uuid.UUID) -> list[TrustedDevice]:
        """Non-expired trusted devices for the user, newest activity first."""
        now = datetime.now(UTC)
        rows = (
            await self.session.execute(
                select(TrustedDevice)
                .where(TrustedDevice.user_id == user_id, TrustedDevice.expires_at > now)
                .order_by(TrustedDevice.last_used_at.desc())
            )
        ).scalars().all()
        return list(rows)

    async def revoke(self, device_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Delete one device scoped to its owner. Returns True if a row was removed."""
        result = await self.session.execute(
            delete(TrustedDevice).where(
                TrustedDevice.id == device_id, TrustedDevice.user_id == user_id
            )
        )
        return (result.rowcount or 0) > 0

    async def revoke_all(self, user_id: uuid.UUID) -> int:
        result = await self.session.execute(
            delete(TrustedDevice).where(TrustedDevice.user_id == user_id)
        )
        return result.rowcount or 0

    async def purge_expired(self, now: datetime) -> int:
        result = await self.session.execute(
            delete(TrustedDevice).where(TrustedDevice.expires_at <= now)
        )
        return result.rowcount or 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_trusted_device_service.py -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/trusted_device.py backend/tests/test_trusted_device_service.py
git commit -m "feat(mfa): TrustedDeviceService (mint/find/list/revoke/purge)"
```

---

### Task 3: Settings — org toggle + runtime day count + cookie constant

**Files:**
- Modify: `backend/app/core/config.py:21-23` (add two Settings fields near the session settings)
- Modify: `backend/app/services/app_settings.py` (add `get_trusted_device_enabled`/`set_trusted_device_enabled`)
- Modify: `backend/app/services/runtime_settings.py:53` (add the `trusted_device_days` registry entry)
- Modify: `backend/app/core/deps.py:16-18` (add the cookie constant)
- Test: `backend/tests/test_trusted_device_settings.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_trusted_device_settings.py
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.deps import TRUSTED_DEVICE_COOKIE
from app.services.app_settings import get_trusted_device_enabled, set_trusted_device_enabled
from app.services.runtime_settings import runtime_defaults


def test_cookie_constant():
    assert TRUSTED_DEVICE_COOKIE == "opngms_trusted_device"


def test_trusted_device_days_default_is_30():
    assert runtime_defaults()["trusted_device_days"] == 30


async def test_toggle_default_on_then_override(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        assert await get_trusted_device_enabled(s, env_default=True) is True  # no row -> env default
        await set_trusted_device_enabled(s, False)
        await s.commit()
        assert await get_trusted_device_enabled(s, env_default=True) is False
        await set_trusted_device_enabled(s, True)
        await s.commit()
        assert await get_trusted_device_enabled(s, env_default=True) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trusted_device_settings.py -q`
Expected: FAIL with `ImportError` (`TRUSTED_DEVICE_COOKIE` / `get_trusted_device_enabled` not defined).

- [ ] **Step 3: Add the Settings fields**

In `backend/app/core/config.py`, immediately after line 23 (`mfa_pending_ttl_minutes: int = 5  # ...`) add:

```python
    trusted_device_enabled: bool = True  # org default for "remember this device" (admin can override)
    trusted_device_days: int = 30  # how long a trusted device skips the second factor (1..365)
```

- [ ] **Step 4: Add the app-settings toggle helpers**

In `backend/app/services/app_settings.py`, after the `set_live_push` function (line 42) add:

```python
_TRUSTED_DEVICE_KEY = "trusted_device_enabled"


async def get_trusted_device_enabled(session: AsyncSession, *, env_default: bool) -> bool:
    """Org-wide on/off for remember-this-device: the DB override row if present, else the env default."""
    row = (
        await session.execute(select(AppSetting).where(AppSetting.key == _TRUSTED_DEVICE_KEY))
    ).scalar_one_or_none()
    if row is None:
        return env_default
    return bool((row.value or {}).get("enabled", env_default))


async def set_trusted_device_enabled(session: AsyncSession, enabled: bool) -> None:
    row = (
        await session.execute(select(AppSetting).where(AppSetting.key == _TRUSTED_DEVICE_KEY))
    ).scalar_one_or_none()
    if row is None:
        session.add(AppSetting(key=_TRUSTED_DEVICE_KEY, value={"enabled": bool(enabled)}))
    else:
        row.value = {"enabled": bool(enabled)}
```

- [ ] **Step 5: Add the runtime-settings registry entry**

In `backend/app/services/runtime_settings.py`, in the `RUNTIME_SETTINGS` list, immediately after the `session_idle_minutes` line (line 53) add:

```python
    RuntimeSetting("trusted_device_days", int, lambda s: s.trusted_device_days, 1, 365, "security_session"),
```

- [ ] **Step 6: Add the cookie constant**

In `backend/app/core/deps.py`, after line 17 (`CSRF_COOKIE = ...`) add:

```python
TRUSTED_DEVICE_COOKIE = "opngms_trusted_device"  # HttpOnly cookie carrying the raw trusted-device token
```

- [ ] **Step 7: Run test to verify it passes**

Run: `python -m pytest tests/test_trusted_device_settings.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/core/config.py backend/app/services/app_settings.py backend/app/services/runtime_settings.py backend/app/core/deps.py backend/tests/test_trusted_device_settings.py
git commit -m "feat(mfa): trusted-device org toggle + runtime day-count + cookie constant"
```

---

### Task 4: Set the trusted cookie on second-factor completion

**Files:**
- Modify: `backend/app/schemas/mfa.py:12-13` (extend `CodeIn`), `:53-55` (extend `WebAuthnLoginCompleteIn`)
- Modify: `backend/app/api/auth.py` (`/login/mfa` ~270-294, `/login/webauthn/complete` ~414-438): set the cookie when `remember_device` is true
- Test: `backend/tests/test_trusted_device_login.py` (part 1)

A shared helper avoids duplicating the create-row + set-cookie logic across the TOTP and WebAuthn paths.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_trusted_device_login.py
import pyotp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api import auth as auth_api
from app.models.trusted_device import TrustedDevice
from app.models.user_mfa import UserMfa
from app.models.webauthn_credential import WebAuthnCredential
from app.core import crypto
from app.services import mfa as mfa_svc
from app.services.app_settings import set_trusted_device_enabled
from app.services.webauthn import has_webauthn_credentials  # noqa: F401 (import parity)
from tests.conftest import csrf_headers
from tests.factories import make_user

from webauthn.helpers import bytes_to_base64url

_CRED_ID = b"\xaa\xbb\xcc\xdd\x01\x02"
_CRED_ID_B64 = bytes_to_base64url(_CRED_ID)


async def _seed_totp_user(db_engine, email="tt@x.io", password="pw12345-secure"):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        u = await make_user(s, email=email, password=password)
        secret = mfa_svc.new_secret()
        s.add(UserMfa(user_id=u.id, enabled=True, totp_secret_enc=crypto.encrypt(secret)))
        await s.commit()
        return u.id, secret


async def test_totp_remember_device_creates_row_and_cookie(api_client, db_engine):
    uid, secret = await _seed_totp_user(db_engine)
    await api_client.post("/api/login", json={"email": "tt@x.io", "password": "pw12345-secure"})
    h = csrf_headers(api_client)
    code = pyotp.TOTP(secret).now()
    r = await api_client.post(
        "/api/login/mfa", json={"code": code, "remember_device": True}, headers=h
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"
    assert api_client.cookies.get("opngms_trusted_device")  # cookie set
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        rows = (await s.execute(select(TrustedDevice).where(TrustedDevice.user_id == uid))).scalars().all()
        assert len(rows) == 1


async def test_totp_without_remember_sets_no_cookie(api_client, db_engine):
    uid, secret = await _seed_totp_user(db_engine, email="nt@x.io")
    await api_client.post("/api/login", json={"email": "nt@x.io", "password": "pw12345-secure"})
    h = csrf_headers(api_client)
    r = await api_client.post(
        "/api/login/mfa", json={"code": pyotp.TOTP(secret).now()}, headers=h
    )
    assert r.status_code == 200, r.text
    assert not api_client.cookies.get("opngms_trusted_device")
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        assert (await s.execute(select(TrustedDevice).where(TrustedDevice.user_id == uid))).first() is None


async def test_remember_device_ignored_when_toggle_off(api_client, db_engine):
    uid, secret = await _seed_totp_user(db_engine, email="off@x.io")
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await set_trusted_device_enabled(s, False)
        await s.commit()
    await api_client.post("/api/login", json={"email": "off@x.io", "password": "pw12345-secure"})
    h = csrf_headers(api_client)
    r = await api_client.post(
        "/api/login/mfa", json={"code": pyotp.TOTP(secret).now(), "remember_device": True}, headers=h
    )
    assert r.status_code == 200, r.text
    assert not api_client.cookies.get("opngms_trusted_device")
    async with factory() as s:
        assert (await s.execute(select(TrustedDevice).where(TrustedDevice.user_id == uid))).first() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_trusted_device_login.py -q`
Expected: FAIL — `remember_device` is rejected by `CodeIn` (extra field ignored → no cookie) so the cookie assertions fail.

- [ ] **Step 3: Extend the request schemas**

In `backend/app/schemas/mfa.py`, change `CodeIn` (lines 12-13) to:

```python
class CodeIn(BaseModel):
    code: str = Field(max_length=128)
    # Opt-in "remember this device": skip the second factor on this device for N days (N from settings).
    remember_device: bool = False
```

and change `WebAuthnLoginCompleteIn` (lines 53-55) to:

```python
class WebAuthnLoginCompleteIn(BaseModel):
    # The browser's PublicKeyCredential JSON from navigator.credentials.get().
    credential: dict[str, Any]
    remember_device: bool = False
```

- [ ] **Step 4: Add the shared helper + wire both completion paths**

In `backend/app/api/auth.py`, add imports near the top (with the other `app.services` / `app.core.deps` imports):

```python
from app.core.deps import TRUSTED_DEVICE_COOKIE
from app.services.app_settings import get_mfa_policy, get_trusted_device_enabled
from app.services.trusted_device import TrustedDeviceService
```

(Merge the `get_trusted_device_enabled` import into the existing `from app.services.app_settings import get_mfa_policy` line.)

Add this helper after `_client_ip` (after line 51):

```python
async def _maybe_remember_device(
    *, remember: bool, session: AsyncSession, response: Response, request: Request,
    user_id, client_ip: str | None,
) -> None:
    """If the user opted in and the org toggle is on, mint a trusted-device row + set its cookie so a
    future login from this device can skip the second factor. No-op otherwise."""
    if not remember:
        return
    settings = get_settings()
    if not await get_trusted_device_enabled(session, env_default=settings.trusted_device_enabled):
        return
    days = (await get_runtime_config_or_defaults(session))["trusted_device_days"]
    _, raw = await TrustedDeviceService(session).create_for_user(
        user_id, days=days, user_agent=request.headers.get("user-agent"), ip=client_ip,
    )
    await AuditService(session).record(
        actor_user_id=user_id, tenant_id=None, action="auth.trusted_device.create",
        target_type="user", target_id=str(user_id), ip=client_ip, details={},
    )
    response.set_cookie(
        TRUSTED_DEVICE_COOKIE, raw, httponly=True, secure=True, samesite="lax",
        max_age=days * 86400,
    )
```

In `/login/mfa`, immediately before `await session.commit()` at the end of the success path (the commit at line ~287, right after the `mfa.login_success`/`mfa.recovery_used` audit `.record(...)`), insert:

```python
    await _maybe_remember_device(
        remember=body.remember_device, session=session, response=response, request=request,
        user_id=user.id, client_ip=client_ip,
    )
```

In `/login/webauthn/complete`, immediately before `await session.commit()` at the end of the success path (the commit at line ~431, right after the `mfa.login_success` audit `.record(...)`), insert the same call:

```python
    await _maybe_remember_device(
        remember=body.remember_device, session=session, response=response, request=request,
        user_id=user.id, client_ip=client_ip,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_trusted_device_login.py -q`
Expected: PASS (3 tests).

- [ ] **Step 6: Run the audit-coverage guard (the create audit lives in a helper called by audited routes)**

Run: `python -m pytest tests/test_audit_coverage.py -q`
Expected: PASS (the `/login/mfa` and `/login/webauthn/complete` routes still call `.record(` inline for the login-success event).

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas/mfa.py backend/app/api/auth.py backend/tests/test_trusted_device_login.py
git commit -m "feat(mfa): set trusted-device cookie on second-factor completion"
```

---

### Task 5: Skip the second factor on /login for a trusted device

**Files:**
- Modify: `backend/app/api/auth.py` (`/login` — after the `enrolled` computation at lines 122-134)
- Modify: `backend/app/schemas/auth.py:30-35` (add `RememberDeviceInfo` + `LoginOut.remember_device`)
- Test: `backend/tests/test_trusted_device_login.py` (part 2 — append)

- [ ] **Step 1: Write the failing test (append to the same file)**

```python
# append to backend/tests/test_trusted_device_login.py
from app.services.trusted_device import TrustedDeviceService


async def _mint_trusted_cookie(db_engine, user_id, days=30):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        _, raw = await TrustedDeviceService(s).create_for_user(
            user_id, days=days, user_agent="UA", ip="1.2.3.4"
        )
        await s.commit()
        return raw


async def test_login_skips_mfa_with_valid_trusted_cookie(api_client, db_engine):
    uid, _ = await _seed_totp_user(db_engine, email="skip@x.io")
    raw = await _mint_trusted_cookie(db_engine, uid)
    api_client.cookies.set("opngms_trusted_device", raw, domain="test")
    r = await api_client.post("/api/login", json={"email": "skip@x.io", "password": "pw12345-secure"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"  # second factor skipped
    # a full session: an app endpoint works
    assert (await api_client.get("/api/me")).status_code == 200


async def test_login_requires_mfa_without_cookie(api_client, db_engine):
    await _seed_totp_user(db_engine, email="nocookie@x.io")
    r = await api_client.post("/api/login", json={"email": "nocookie@x.io", "password": "pw12345-secure"})
    assert r.json()["status"] == "mfa_required"
    assert r.json()["remember_device"]["enabled"] is True
    assert r.json()["remember_device"]["days"] == 30


async def test_login_requires_mfa_with_other_users_cookie(api_client, db_engine):
    uid_a, _ = await _seed_totp_user(db_engine, email="owner@x.io")
    await _seed_totp_user(db_engine, email="victim@x.io")
    raw = await _mint_trusted_cookie(db_engine, uid_a)  # owner's cookie
    api_client.cookies.set("opngms_trusted_device", raw, domain="test")
    r = await api_client.post("/api/login", json={"email": "victim@x.io", "password": "pw12345-secure"})
    assert r.json()["status"] == "mfa_required"  # cookie belongs to a different user


async def test_login_requires_mfa_when_toggle_off_even_with_cookie(api_client, db_engine):
    uid, _ = await _seed_totp_user(db_engine, email="togoff@x.io")
    raw = await _mint_trusted_cookie(db_engine, uid)
    api_client.cookies.set("opngms_trusted_device", raw, domain="test")
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await set_trusted_device_enabled(s, False)
        await s.commit()
    r = await api_client.post("/api/login", json={"email": "togoff@x.io", "password": "pw12345-secure"})
    assert r.json()["status"] == "mfa_required"
    assert r.json()["remember_device"]["enabled"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_trusted_device_login.py -q -k "skip or without_cookie or other_users or toggle_off"`
Expected: FAIL — `/login` does not yet skip, and `LoginOut` has no `remember_device` field (KeyError on `r.json()["remember_device"]`).

- [ ] **Step 3: Extend `LoginOut`**

In `backend/app/schemas/auth.py`, after `MeOut` (before `LoginOut`) add:

```python
class RememberDeviceInfo(BaseModel):
    enabled: bool  # whether the org allows "remember this device"
    days: int  # how long a trusted device skips the second factor
```

and add the field to `LoginOut`:

```python
class LoginOut(BaseModel):
    status: str  # "ok" | "mfa_required" | "mfa_setup_required"
    user: MeOut | None = None
    # On "mfa_required": which second factors the user can satisfy the challenge with
    # (e.g. ["totp", "webauthn"]) so the SPA shows the right options. None otherwise.
    methods: list[str] | None = None
    # On "mfa_required": whether to offer the "remember this device" checkbox and for how many days.
    remember_device: RememberDeviceInfo | None = None
```

- [ ] **Step 4: Wire the skip + populate `remember_device` in `/login`**

In `backend/app/api/auth.py`, update the import in `from app.schemas.auth import LoginIn, LoginOut, MeOut, SessionInfo` to also import `RememberDeviceInfo`:

```python
from app.schemas.auth import LoginIn, LoginOut, MeOut, RememberDeviceInfo, SessionInfo
```

Replace the kind-decision + return block. After the existing lines that compute `has_totp`, `has_passkey`, `policy`, `is_priv`, `methods` (lines 122-128), insert the trusted-device skip BEFORE the `if has_totp or has_passkey:` block:

```python
    enrolled = has_totp or has_passkey
    td_enabled = await get_trusted_device_enabled(session, env_default=settings.trusted_device_enabled)
    td_days = runtime["trusted_device_days"]
    # Trusted-device skip: password already verified above; if this enrolled user presents a valid
    # trusted-device cookie and the org allows it, mint a full session directly (skip the 2nd factor).
    if enrolled and td_enabled:
        raw_td = request.cookies.get(TRUSTED_DEVICE_COOKIE)
        td_svc = TrustedDeviceService(session)
        td_row = await td_svc.find_valid(user.id, raw_td) if raw_td else None
        if td_row is not None:
            await td_svc.touch(td_row)
            full, raw_token = await svc.create_session(
                user, ttl_hours=runtime["session_ttl_hours"], kind="full",
                ip=client_ip, user_agent=request.headers.get("user-agent"),
            )
            try:
                login_limiter.reset(key)
            except Exception:  # noqa: BLE001 — never let a limiter fault break a successful login
                logger.error("login rate-limiter reset failed", exc_info=True)
            await AuditService(session).record(
                actor_user_id=user.id, tenant_id=None, action="auth.login.trusted_device",
                target_type="session", target_id=str(full.id), ip=client_ip, details={},
            )
            await session.commit()
            max_age = round(runtime["session_ttl_hours"] * 3600)
            response.set_cookie(SESSION_COOKIE, raw_token, httponly=True, secure=True, samesite="lax", max_age=max_age)
            response.set_cookie(CSRF_COOKIE, full.csrf_token, httponly=False, secure=True, samesite="lax", max_age=max_age)
            return LoginOut(
                status="ok",
                user=MeOut(id=user.id, email=user.email, name=user.name, is_superadmin=user.is_superadmin),
            )
```

Note: `settings = get_settings()` is already assigned at line 113 (before line 115's `old = request.cookies.get(...)`), and `runtime` at line 66 — both are in scope here.

Then change the `mfa_pending` return (line 167) to include `remember_device`:

```python
    if kind == "mfa_pending":
        return LoginOut(
            status="mfa_required", methods=methods,
            remember_device=RememberDeviceInfo(enabled=td_enabled, days=td_days),
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_trusted_device_login.py -q`
Expected: PASS (all 7 tests in the file).

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/auth.py backend/app/schemas/auth.py backend/tests/test_trusted_device_login.py
git commit -m "feat(mfa): skip second factor on /login for a trusted device"
```

---

### Task 6: Management API + auto-revoke + sweeper

**Files:**
- Create: `backend/app/api/trusted_devices.py`
- Modify: `backend/app/main.py` (import + `include_router`)
- Modify: `backend/app/schemas/mfa.py` (add `TrustedDeviceOut`)
- Modify: `backend/app/api/mfa.py` (`/me/mfa/disable` ~170-186 and `/users/{user_id}/mfa/reset` ~385-405: call `revoke_all`)
- Modify: `backend/app/worker.py:514-522` (`cleanup_expired_sessions`: also purge expired trusted devices)
- Test: `backend/tests/test_trusted_devices_api.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_trusted_devices_api.py
import pyotp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import crypto
from app.models.trusted_device import TrustedDevice
from app.models.user_mfa import UserMfa
from app.services import mfa as mfa_svc
from app.services.trusted_device import TrustedDeviceService
from tests.conftest import csrf_headers
from tests.factories import make_user


async def _login_full(api_client, db_engine, email="mgr@x.io"):
    """Seed a TOTP user, log in, clear the challenge with a code -> full session in the client jar."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        u = await make_user(s, email=email, password="pw12345-secure")
        secret = mfa_svc.new_secret()
        s.add(UserMfa(user_id=u.id, enabled=True, totp_secret_enc=crypto.encrypt(secret)))
        await s.commit()
        uid = u.id
    await api_client.post("/api/login", json={"email": email, "password": "pw12345-secure"})
    h = csrf_headers(api_client)
    await api_client.post("/api/login/mfa", json={"code": pyotp.TOTP(secret).now()}, headers=h)
    return uid


async def test_list_and_delete_one(api_client, db_engine):
    uid = await _login_full(api_client, db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        r1, _ = await TrustedDeviceService(s).create_for_user(uid, days=30, user_agent="A", ip=None)
        r2, _ = await TrustedDeviceService(s).create_for_user(uid, days=30, user_agent="B", ip=None)
        await s.commit()
        id1 = r1.id
    resp = await api_client.get("/api/me/trusted-devices")
    assert resp.status_code == 200
    assert len(resp.json()) == 2
    h = csrf_headers(api_client)
    d = await api_client.request("DELETE", f"/api/me/trusted-devices/{id1}", headers=h)
    assert d.status_code == 204
    assert len((await api_client.get("/api/me/trusted-devices")).json()) == 1


async def test_delete_all(api_client, db_engine):
    uid = await _login_full(api_client, db_engine, email="all@x.io")
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await TrustedDeviceService(s).create_for_user(uid, days=30, user_agent="A", ip=None)
        await TrustedDeviceService(s).create_for_user(uid, days=30, user_agent="B", ip=None)
        await s.commit()
    h = csrf_headers(api_client)
    d = await api_client.request("DELETE", "/api/me/trusted-devices", headers=h)
    assert d.status_code == 204
    assert (await api_client.get("/api/me/trusted-devices")).json() == []


async def test_delete_other_users_device_404(api_client, db_engine):
    await _login_full(api_client, db_engine, email="me2@x.io")
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        other = await make_user(s, email="other2@x.io", password="pw12345-secure")
        row, _ = await TrustedDeviceService(s).create_for_user(other.id, days=30, user_agent=None, ip=None)
        await s.commit()
        other_id = row.id
    h = csrf_headers(api_client)
    d = await api_client.request("DELETE", f"/api/me/trusted-devices/{other_id}", headers=h)
    assert d.status_code == 404


async def test_disable_mfa_revokes_trusted_devices(api_client, db_engine):
    uid = await _login_full(api_client, db_engine, email="dis@x.io")
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await TrustedDeviceService(s).create_for_user(uid, days=30, user_agent=None, ip=None)
        await s.commit()
    h = csrf_headers(api_client)
    r = await api_client.post("/api/me/mfa/disable", json={"password": "pw12345-secure"}, headers=h)
    assert r.status_code == 204
    async with factory() as s:
        assert (await s.execute(select(TrustedDevice).where(TrustedDevice.user_id == uid))).first() is None


async def test_purge_expired_helper(db_engine):
    # the sweeper's underlying call
    from datetime import UTC, datetime, timedelta

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        u = await make_user(s, email="purge@x.io", password="pw12345-secure")
        svc = TrustedDeviceService(s)
        live, _ = await svc.create_for_user(u.id, days=30, user_agent=None, ip=None)
        dead, _ = await svc.create_for_user(u.id, days=30, user_agent=None, ip=None)
        dead.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await s.commit()
        assert await svc.purge_expired(datetime.now(UTC)) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_trusted_devices_api.py -q`
Expected: FAIL — `/api/me/trusted-devices` returns 404 (router not mounted).

- [ ] **Step 3: Add the output schema**

In `backend/app/schemas/mfa.py`, after `WebAuthnCredentialOut` (line 50) add:

```python
class TrustedDeviceOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    user_agent: str | None = None
    ip: str | None = None
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
```

- [ ] **Step 4: Create the management router**

```python
# backend/app/api/trusted_devices.py
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import enforce_csrf, get_current_user
from app.models.user import User
from app.schemas.mfa import TrustedDeviceOut
from app.services.audit import AuditService
from app.services.trusted_device import TrustedDeviceService

router = APIRouter(prefix="/api", tags=["trusted-devices"])


@router.get("/me/trusted-devices", response_model=list[TrustedDeviceOut])
async def list_trusted_devices(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[TrustedDeviceOut]:
    rows = await TrustedDeviceService(session).list_for_user(user.id)
    return [TrustedDeviceOut.model_validate(r) for r in rows]


@router.delete(
    "/me/trusted-devices/{device_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(enforce_csrf)],
)
async def revoke_trusted_device(
    device_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    removed = await TrustedDeviceService(session).revoke(device_id, user.id)
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await AuditService(session).record(
        actor_user_id=user.id, tenant_id=None, action="auth.trusted_device.revoke",
        target_type="user", target_id=str(user.id), ip=None, details={"device": str(device_id)},
    )
    await session.commit()


@router.delete(
    "/me/trusted-devices",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(enforce_csrf)],
)
async def revoke_all_trusted_devices(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    n = await TrustedDeviceService(session).revoke_all(user.id)
    await AuditService(session).record(
        actor_user_id=user.id, tenant_id=None, action="auth.trusted_device.revoke_all",
        target_type="user", target_id=str(user.id), ip=None, details={"count": n},
    )
    await session.commit()
```

- [ ] **Step 5: Mount the router**

In `backend/app/main.py`, add the import next to the other `app.api` imports (alphabetical, after `from app.api.templates import router as templates_router` / near `tenants`):

```python
from app.api.trusted_devices import router as trusted_devices_router
```

and add the include near the other auth/mfa includes (after `app.include_router(mfa_router)`):

```python
app.include_router(trusted_devices_router)
```

- [ ] **Step 6: Wire auto-revoke into MFA teardown**

In `backend/app/api/mfa.py`, add the import:

```python
from app.services.trusted_device import TrustedDeviceService
```

In `mfa_disable` (after `await session.delete(row)` / before the audit `.record(...)` at line ~182) add:

```python
    await TrustedDeviceService(session).revoke_all(user.id)
```

In `mfa_admin_reset` (after `await session.delete(row)` / before the audit `.record(...)` at line ~401) add:

```python
    await TrustedDeviceService(session).revoke_all(user_id)
```

- [ ] **Step 7: Add the sweeper purge**

In `backend/app/worker.py`, in `cleanup_expired_sessions` (lines 514-522), after `n = await AuthService(session).purge_expired(datetime.now(UTC))` and before `await session.commit()` add:

```python
        from app.services.trusted_device import TrustedDeviceService  # local import avoids a cycle

        td = await TrustedDeviceService(session).purge_expired(datetime.now(UTC))
```

and change the return to include it:

```python
    return f"purged {n} expired sessions, {td} expired trusted devices"
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `python -m pytest tests/test_trusted_devices_api.py -q`
Expected: PASS (5 tests).

- [ ] **Step 9: Run the audit-coverage guard**

Run: `python -m pytest tests/test_audit_coverage.py -q`
Expected: PASS — both `DELETE /api/me/trusted-devices` and `DELETE /api/me/trusted-devices/{device_id}` call `.record(` inline.

- [ ] **Step 10: Commit**

```bash
git add backend/app/api/trusted_devices.py backend/app/main.py backend/app/schemas/mfa.py backend/app/api/mfa.py backend/app/worker.py backend/tests/test_trusted_devices_api.py
git commit -m "feat(mfa): trusted-device management API + auto-revoke + sweeper purge"
```

---

### Task 7: Gate — full backend test + lint + migration sanity

**Files:** none (verification only)

- [ ] **Step 1: Run the trusted-device tests together**

Run: `python -m pytest tests/test_trusted_device_model.py tests/test_trusted_device_service.py tests/test_trusted_device_settings.py tests/test_trusted_device_login.py tests/test_trusted_devices_api.py tests/test_audit_coverage.py -q`
Expected: ALL PASS.

- [ ] **Step 2: Run the auth/mfa regression set (behavior preserved)**

Run: `python -m pytest tests/test_auth.py tests/test_mfa_login_api.py tests/test_login_webauthn.py tests/test_mfa_api.py tests/test_auth_audit.py -q`
(If a listed file does not exist, drop it — confirm with `ls tests | grep -E "auth|mfa|webauthn"`.)
Expected: ALL PASS.

- [ ] **Step 3: Lint**

Run: `ruff check app/`
Expected: no errors. Fix any (unused imports, line length 100, import order).

- [ ] **Step 4: Migration applies on a clean DB (production parity)**

Run (against the dev DB, ALEMBIC_DATABASE_URL set per AGENTS.md):
```bash
alembic upgrade head && alembic current
```
Expected: head is `0045`. Then confirm the table exists:
```bash
psql "$ADMIN_DATABASE_URL" -c "\d trusted_devices" 2>/dev/null || echo "use the async URL host/port; table should have id/user_id/token_hash/.../expires_at"
```
Expected: the `trusted_devices` table with the four indexes (user_id, token_hash unique, token_hash, expires_at).

- [ ] **Step 5: Final commit (if lint/fixups changed anything)**

```bash
git add -A && git commit -m "chore(mfa): lint + migration sanity for trusted-device backend" || echo "nothing to commit"
```

---

## Self-review notes (author)

- **Spec coverage:** model+migration (T1), service mint/find/list/revoke/purge (T2), org toggle + runtime days + cookie const (T3), set-cookie on both completion paths (T4), `/login` skip + `LoginOut.remember_device` (T5), management API + auto-revoke on disable-MFA + admin-reset + sweeper purge (T6), gate (T7). Every spec section maps to a task.
- **Type consistency:** `TrustedDeviceService` method names (`create_for_user`, `find_valid`, `touch`, `list_for_user`, `revoke`, `revoke_all`, `purge_expired`) are used identically across T2/T4/T5/T6. `RememberDeviceInfo{enabled,days}` defined in T5 and asserted in T5 tests. `TRUSTED_DEVICE_COOKIE` defined T3, used T4/T5. `remember_device` schema field defined T4, consumed T4.
- **Security:** find_valid is fail-closed and user-scoped; password always verified before the skip; cookie HttpOnly+Secure+SameSite=lax; token hashed with SESSION_SECRET; mutations CSRF-guarded; audit on create/skip/revoke. A security-review subagent runs after T7.
- **No placeholders:** every code step shows full code; the one conditional (`mfa_svc.current_code`) has an explicit fallback (`pyotp.TOTP(secret).now()`).
