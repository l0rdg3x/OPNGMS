# WebAuthn MFA (PR1 — backend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Back-end support for registering WebAuthn passkeys and using them as a second login factor alongside TOTP — a user satisfies the MFA challenge with a passkey **or** a TOTP code.

**Architecture:** A new `webauthn_credential` table (N per user) + a per-ceremony `webauthn_challenge` on the session row. `app/services/webauthn.py` wraps `py_webauthn`'s registration/authentication ceremonies. New register/login/list/delete endpoints; the login decision treats "has a confirmed TOTP **or** ≥1 passkey" as enrolled. RP ID / origin come from runtime settings (registration disabled until set).

**Tech Stack:** Python 3.14 / SQLAlchemy / Alembic / **py_webauthn** (`webauthn` on PyPI) / pytest. Spec: `docs/superpowers/specs/2026-06-16-webauthn-mfa-design.md`. Frontend = PR2 (separate).

---

## File structure

| File | Change |
|------|--------|
| `backend/pyproject.toml` | add `webauthn` dependency |
| `backend/app/models/webauthn_credential.py` | new model |
| `backend/app/models/session.py` | + nullable `webauthn_challenge` |
| `backend/migrations/versions/0044_webauthn.py` | new (down_revision `0043`) |
| `backend/app/services/webauthn.py` | new — ceremony wrappers + `WebAuthnError` |
| `backend/app/services/webauthn_settings.py` | new — read `webauthn_rp_id`/`rp_name`/`origin` runtime settings + `is_configured()` |
| `backend/app/services/mfa.py` | + `has_webauthn(session, user_id)` helper (or put in webauthn.py) |
| `backend/app/api/mfa.py` | register begin/complete, list, delete, status block |
| `backend/app/api/auth.py` | login enrolled-OR-passkey; `/login/webauthn/begin`+`/complete`; `LoginOut.methods` |
| `backend/app/schemas/mfa.py` (or auth) | request/response models |
| `backend/tests/test_webauthn_service.py`, `test_webauthn_api.py`, `test_login_webauthn.py`, `test_webauthn_migration.py` | new |

---

## Task 1: dependency + model + migration

**Files:** `backend/pyproject.toml`; `backend/app/models/webauthn_credential.py`; `backend/app/models/session.py`; `backend/migrations/versions/0044_webauthn.py`; `backend/app/models/__init__.py` (register the model); Test `backend/tests/test_webauthn_migration.py`.

- [ ] **Step 1: Add the dependency.** In `backend/pyproject.toml` `[project].dependencies`, add `"webauthn>=2.0"`. Install into the venv: `cd backend && . .venv/bin/activate && pip install 'webauthn>=2.0'`. Confirm: `python -c "import webauthn; print(webauthn.__version__)"`.

- [ ] **Step 2: Write the failing migration test**
```python
# backend/tests/test_webauthn_migration.py
from sqlalchemy import text


async def test_webauthn_schema(db_engine):
    async with db_engine.begin() as conn:
        cred = set((await conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='webauthn_credential'"
        ))).scalars().all())
        sess = set((await conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='sessions'"
        ))).scalars().all())
    assert {"id", "user_id", "credential_id", "public_key", "sign_count", "transports",
            "name", "aaguid", "created_at", "last_used_at"} <= cred
    assert "webauthn_challenge" in sess
```

- [ ] **Step 3: Run to verify it fails** — `cd backend && python -m pytest tests/test_webauthn_migration.py -q` (FAIL: table/column absent).

- [ ] **Step 4: Create the model** `app/models/webauthn_credential.py`:
```python
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, LargeBinary, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WebAuthnCredential(Base):
    """One registered WebAuthn authenticator (passkey / security key) for a user. Public key + sign
    count only — no private/secret material lives here. `credential_id` is globally unique."""

    __tablename__ = "webauthn_credential"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    credential_id: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary)
    sign_count: Mapped[int] = mapped_column(BigInteger, default=0)
    transports: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    name: Mapped[str] = mapped_column(Text, default="")
    aaguid: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```
Add to `app/models/session.py` (after `csrf_token`): `webauthn_challenge: Mapped[str | None] = mapped_column(String, nullable=True, default=None)`. Register `WebAuthnCredential` in `app/models/__init__.py` (mirror how the other models are imported there).

- [ ] **Step 5: Create the migration** `migrations/versions/0044_webauthn.py`:
```python
"""webauthn_credential table + sessions.webauthn_challenge"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webauthn_credential",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("credential_id", sa.LargeBinary(), nullable=False, unique=True),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("transports", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("aaguid", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("sessions", sa.Column("webauthn_challenge", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "webauthn_challenge")
    op.drop_table("webauthn_credential")
```

- [ ] **Step 6: Run to verify it passes** (test DB builds schema from metadata) — `python -m pytest tests/test_webauthn_migration.py -q` (PASS).

- [ ] **Step 7: Commit** — `git add pyproject.toml app/models/webauthn_credential.py app/models/session.py app/models/__init__.py migrations/versions/0044_webauthn.py tests/test_webauthn_migration.py && git commit -m "feat(mfa): webauthn_credential model + session challenge + migration 0044"`

---

## Task 2: WebAuthn settings (RP ID / origin)

**Files:** Create `backend/app/services/webauthn_settings.py`; Test `backend/tests/test_webauthn_settings.py`. Read `app/services/app_settings.py` + `app/core/config.py` first to mirror the env-default + DB-override runtime-settings pattern (the same one `get_mfa_policy` / the System runtime settings use).

- [ ] **Step 1: Write the failing test**
```python
# backend/tests/test_webauthn_settings.py
from app.services.webauthn_settings import WebAuthnConfig, get_webauthn_config


async def test_unconfigured_is_not_usable(db_session):
    cfg = await get_webauthn_config(db_session)
    assert isinstance(cfg, WebAuthnConfig)
    assert cfg.is_configured() is (bool(cfg.rp_id) and bool(cfg.origin))
```

- [ ] **Step 2: Run to verify it fails** — `python -m pytest tests/test_webauthn_settings.py -q` (ImportError).

- [ ] **Step 3: Implement** `app/services/webauthn_settings.py`:
```python
"""RP ID / name / origin for WebAuthn, from runtime settings (env default + DB override). WebAuthn
needs a stable HTTPS domain; until rp_id + origin are set, registration is refused."""
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.app_settings import get_setting  # the generic runtime-setting reader

_RP_ID = "webauthn_rp_id"
_RP_NAME = "webauthn_rp_name"
_ORIGIN = "webauthn_origin"


@dataclass(frozen=True)
class WebAuthnConfig:
    rp_id: str
    rp_name: str
    origin: str

    def is_configured(self) -> bool:
        return bool(self.rp_id) and bool(self.origin)


async def get_webauthn_config(session: AsyncSession) -> WebAuthnConfig:
    rp_id = (await get_setting(session, _RP_ID)) or ""
    rp_name = (await get_setting(session, _RP_NAME)) or "OPNGMS"
    origin = (await get_setting(session, _ORIGIN)) or ""
    return WebAuthnConfig(rp_id=rp_id, rp_name=rp_name, origin=origin)
```
> Adapt `get_setting` to the actual runtime-settings reader in `app/services/app_settings.py` (it may be `get_app_setting`, or a registry with env defaults — read the file and use the real function; register `webauthn_rp_id`/`rp_name`/`origin` in whatever registry the System page reads so they appear there with env defaults `WEBAUTHN_RP_ID`/`WEBAUTHN_RP_NAME`/`WEBAUTHN_ORIGIN`).

- [ ] **Step 4: Run to verify it passes** — `python -m pytest tests/test_webauthn_settings.py -q` (PASS).

- [ ] **Step 5: Commit** — `git add app/services/webauthn_settings.py tests/test_webauthn_settings.py && git commit -m "feat(mfa): webauthn rp-id/name/origin runtime settings"`

---

## Task 3: ceremony wrappers — `app/services/webauthn.py`

**Files:** Create `backend/app/services/webauthn.py`; Test `backend/tests/test_webauthn_service.py`.

Read py_webauthn's API for the installed version first (`python -c "import webauthn, inspect; print([n for n in dir(webauthn) if not n.startswith('_')])"`), then implement thin wrappers. The public functions used: `generate_registration_options`, `verify_registration_response`, `generate_authentication_options`, `verify_authentication_response`, `options_to_json`, and the `webauthn.helpers` base64url codecs.

- [ ] **Step 1: Write the failing tests** — use py_webauthn's own structures. For verify tests, drive a *software authenticator* helper (py_webauthn ships test fixtures under `tests/`, or build minimal ones), OR monkeypatch `webauthn.verify_*` to assert our wrapper maps results/raises correctly. Minimum tests:
```python
# backend/tests/test_webauthn_service.py
import pytest

from app.services import webauthn as wa


def test_registration_options_includes_challenge_and_json():
    opts_json, challenge = wa.registration_options(
        user_id=b"\x01\x02", user_name="a@x.io", rp_id="opngms.test",
        rp_name="OPNGMS", existing_cred_ids=[])
    assert isinstance(opts_json, str) and "challenge" in opts_json
    assert isinstance(challenge, str) and challenge  # base64url, persisted on the session


def test_authentication_options_includes_challenge():
    opts_json, challenge = wa.authentication_options(rp_id="opngms.test", allow_cred_ids=[b"\xaa"])
    assert "challenge" in opts_json and challenge


def test_verify_authentication_rejects_non_increasing_sign_count(monkeypatch):
    class _V:  # what py_webauthn returns
        new_sign_count = 5
    monkeypatch.setattr(wa, "_verify_auth_raw", lambda **k: _V())
    # current stored sign_count 5 -> new 5 is NOT an increase -> reject
    with pytest.raises(wa.WebAuthnError):
        wa.verify_authentication(response={}, challenge="c", rp_id="r", origin="o",
                                 public_key=b"\x00", sign_count=5)
```
(Structure `webauthn.py` so the raw py_webauthn call is a tiny `_verify_auth_raw`/`_verify_reg_raw` indirection that tests can monkeypatch — this keeps the wrapper logic, esp. the **sign-count strictly-increasing** check, unit-testable without a real authenticator.)

- [ ] **Step 2: Run to verify it fails** — `python -m pytest tests/test_webauthn_service.py -q` (ImportError / AttributeError).

- [ ] **Step 3: Implement** `app/services/webauthn.py`:
```python
"""Thin wrappers over py_webauthn for the registration + authentication ceremonies. No
private/secret material is handled here (WebAuthn is public-key); challenges + credential ids are
base64url. Raises WebAuthnError on any verification mismatch."""
from __future__ import annotations

import webauthn
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor, ResidentKeyRequirement,
    UserVerificationRequirement,
)


class WebAuthnError(Exception):
    """A WebAuthn ceremony failed verification. Safe to surface; carries no key material."""


def registration_options(*, user_id: bytes, user_name: str, rp_id: str, rp_name: str,
                         existing_cred_ids: list[bytes]) -> tuple[str, str]:
    opts = webauthn.generate_registration_options(
        rp_id=rp_id, rp_name=rp_name, user_id=user_id, user_name=user_name,
        exclude_credentials=[PublicKeyCredentialDescriptor(id=c) for c in existing_cred_ids],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.DISCOURAGED,
            user_verification=UserVerificationRequirement.PREFERRED),
    )
    return webauthn.options_to_json(opts), bytes_to_base64url(opts.challenge)


def _verify_reg_raw(**kw):  # indirection for tests
    return webauthn.verify_registration_response(**kw)


def verify_registration(*, response: dict, challenge: str, rp_id: str, origin: str):
    try:
        v = _verify_reg_raw(credential=response, expected_challenge=_b64(challenge),
                            expected_rp_id=rp_id, expected_origin=origin)
    except Exception as exc:  # py_webauthn raises various verification errors
        raise WebAuthnError("registration verification failed") from exc
    return v  # has .credential_id, .credential_public_key, .sign_count, .aaguid


def authentication_options(*, rp_id: str, allow_cred_ids: list[bytes]) -> tuple[str, str]:
    opts = webauthn.generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[PublicKeyCredentialDescriptor(id=c) for c in allow_cred_ids],
        user_verification=UserVerificationRequirement.PREFERRED)
    return webauthn.options_to_json(opts), bytes_to_base64url(opts.challenge)


def _verify_auth_raw(**kw):  # indirection for tests
    return webauthn.verify_authentication_response(**kw)


def verify_authentication(*, response: dict, challenge: str, rp_id: str, origin: str,
                         public_key: bytes, sign_count: int) -> int:
    try:
        v = _verify_auth_raw(credential=response, expected_challenge=_b64(challenge),
                            expected_rp_id=rp_id, expected_origin=origin,
                            credential_public_key=public_key, credential_current_sign_count=sign_count)
    except Exception as exc:
        raise WebAuthnError("authentication verification failed") from exc
    # Anti-cloned-authenticator: many authenticators keep a monotonic counter; reject a non-increase
    # (unless the authenticator reports 0/0, the documented "no counter" case).
    if not (v.new_sign_count > sign_count or (v.new_sign_count == 0 and sign_count == 0)):
        raise WebAuthnError("sign count did not increase")
    return v.new_sign_count


def _b64(value: str) -> bytes:
    from webauthn.helpers import base64url_to_bytes
    return base64url_to_bytes(value)
```
> The exact py_webauthn struct/field names can differ slightly by version (e.g. `user_id` may want `bytes`, `verify_*` return-field names). Adapt to the installed version's signatures (you imported it in Task 1) — keep the wrapper contract (the 4 functions + `WebAuthnError` + the sign-count check) identical to what the API/tests call.

- [ ] **Step 4: Run to verify it passes** — `python -m pytest tests/test_webauthn_service.py -q` (PASS).

- [ ] **Step 5: Commit** — `git add app/services/webauthn.py tests/test_webauthn_service.py && git commit -m "feat(mfa): webauthn ceremony wrappers (sign-count-increase enforced)"`

---

## Task 4: registration + management API

**Files:** Modify `backend/app/api/mfa.py`, `backend/app/schemas/mfa.py` (or a new `schemas/webauthn.py`); Test `backend/tests/test_webauthn_api.py`.

Endpoints (all under the existing `/api` router; mutations get `Depends(enforce_csrf)`):
- `POST /me/mfa/webauthn/register/begin` — `get_enrollment_ctx`; `cfg = await get_webauthn_config(session)`; if not `cfg.is_configured()` → `HTTPException(409, "WebAuthn not configured")`; build `registration_options(user_id=user.id.bytes, user_name=user.email, rp_id=cfg.rp_id, rp_name=cfg.rp_name, existing_cred_ids=[c.credential_id for c in user_creds])`; store the returned challenge on `sess.webauthn_challenge`; commit; return the options JSON.
- `POST /me/mfa/webauthn/register/complete` — `get_enrollment_ctx`; require `sess.webauthn_challenge`; `v = verify_registration(response=body.credential, challenge=sess.webauthn_challenge, rp_id=cfg.rp_id, origin=cfg.origin)`; persist `WebAuthnCredential(user_id, credential_id=v.credential_id, public_key=v.credential_public_key, sign_count=v.sign_count, transports=body.transports, name=body.name or "passkey", aaguid=str(v.aaguid) if v.aaguid else None)`; clear `sess.webauthn_challenge`; if `sess.kind == "mfa_setup"` → `sess.kind = "full"` (enrollment satisfied, mirroring `/me/mfa/confirm`); audit `mfa.webauthn.add`; commit.
- `GET /me/mfa/webauthn/credentials` → list `{id, name, created_at, last_used_at}` (no `public_key`/`credential_id` bytes).
- `DELETE /me/mfa/webauthn/credentials/{cred_id}` — `get_current_user` + `enforce_csrf`; delete the row if it belongs to the user; **last-factor guard**: if removing it would leave the user with no TOTP and no passkeys while the policy requires MFA for them, refuse `409` (read the existing TOTP-disable guard pattern; reuse the policy check from `auth.py`/`app_settings`). Audit `mfa.webauthn.remove`.
- Extend `GET /me/mfa` (`MfaStatusOut`) with `webauthn: {configured: bool, credentials: int}`.

- [ ] **Step 1: Write the failing tests** `backend/tests/test_webauthn_api.py` — mirror `tests/test_smtp_api.py`/the existing MFA tests for the auth fixture + CSRF. Stub `app.services.webauthn.verify_registration` (monkeypatch) to return an object with `credential_id`/`credential_public_key`/`sign_count`/`aaguid` so no real authenticator is needed. Tests:
  - `register/begin` returns options + persists a challenge; returns `409` when unconfigured (set no rp_id).
  - `register/complete` (configured + stubbed verify) creates a credential and, from an `mfa_setup` session, flips it to `full`.
  - `GET credentials` lists names but no key bytes.
  - `DELETE` removes a credential; the last-factor guard returns `409` when policy=all and it is the only factor.
  - `GET /me/mfa` shows `webauthn.configured` + count.

- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement** the endpoints + schemas per the contract above (full code; reuse `get_enrollment_ctx`, `get_current_user`, `AuditService`, `get_webauthn_config`, the service wrappers).
- [ ] **Step 4: Run to verify it passes** — `python -m pytest tests/test_webauthn_api.py -q`.
- [ ] **Step 5: Commit** — `git commit -m "feat(mfa): webauthn registration + management API (config-gated, last-factor guard)"`

---

## Task 5: login with a passkey + enrolled-OR decision

**Files:** Modify `backend/app/api/auth.py`, the `LoginOut` schema; Test `backend/tests/test_login_webauthn.py`.

- [ ] **Step 1: Write the failing tests** — a user with a passkey (and no TOTP) logging in gets `status="mfa_required"` with `methods` including `"webauthn"`; `/login/webauthn/begin` returns options on the `mfa_pending` session; `/login/webauthn/complete` (stubbed `verify_authentication` → a new sign count) bumps the credential + mints a `full` session (the response sets the session cookie and `status="ok"`). Mirror `tests/`'s existing login/mfa test for the mfa_pending session setup.

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement.**
  - Add `has_webauthn_credentials(session, user_id) -> bool` (a `SELECT 1 ... LIMIT 1` on `webauthn_credential`) — put it in `app/services/webauthn.py` or a small `mfa` helper.
  - In `auth.py` login (~line 118), change the enrolled test to `enrolled = (mfa_row and mfa_row.enabled) or await has_webauthn_credentials(session, user.id)`; when `kind == "mfa_pending"`, set `LoginOut.methods` = the available list (`["totp"]` if `mfa_row.enabled`, plus `["webauthn"]` if the user has passkeys). Add `methods: list[str] | None = None` to `LoginOut`.
  - `POST /login/webauthn/begin` (`sess` via `get_current_session`, require `sess.kind == "mfa_pending"`): build `authentication_options(rp_id=cfg.rp_id, allow_cred_ids=[c.credential_id for c in user_creds])`, store challenge on `sess.webauthn_challenge`, return options. `409` if WebAuthn unconfigured.
  - `POST /login/webauthn/complete` (`enforce_csrf`, mfa_pending session): look up the credential by the response's raw id; `new_count = verify_authentication(response=body.credential, challenge=sess.webauthn_challenge, rp_id=cfg.rp_id, origin=cfg.origin, public_key=cred.public_key, sign_count=cred.sign_count)`; update `cred.sign_count = new_count`, `cred.last_used_at = now`, clear `sess.webauthn_challenge`; then **mint a fresh `full` session** + set cookies + delete the `mfa_pending` one, exactly like `/login/mfa` does (copy that block — anti-fixation rotation); audit `auth.login`; return `LoginOut(status="ok", user=...)`.

- [ ] **Step 4: Run to verify it passes** — `python -m pytest tests/test_login_webauthn.py -q` + the existing `tests/` login/mfa tests still green.

- [ ] **Step 5: Commit** — `git commit -m "feat(mfa): passkey login + enrolled=TOTP-or-passkey decision"`

---

## Task 6: gate

- [ ] **Step 1:** `cd backend && python -m pytest -q && ruff check app/` — all green, clean.
- [ ] **Step 2:** Commit any lint fixups (only if needed).

---

## Self-review (plan vs spec)

- **Spec coverage:** dependency + model + challenge (T1) ✓; RP-id/origin settings, registration-disabled-until-set (T2, T4 409) ✓; ceremony wrappers + sign-count-increase (T3) ✓; register/complete/list/delete + last-factor guard + status block (T4) ✓; passkey login + enrolled-OR + mint-full-session rotation + methods (T5) ✓; security (challenge on session, single-use/clear, origin+RP verify, no logging, CSRF) — embedded in T3/T4/T5 ✓; gate (T6) ✓. Frontend = PR2 (out of this plan). Security review = after the PR (controller step).
- **Placeholder scan:** the py_webauthn struct-name + runtime-settings-reader "adapt to the installed version / real function" notes are explicit read-the-source instructions (the libraries' exact signatures can't be pinned from here), not vague handwaving; every OPNGMS-side contract (function names, fields, endpoints, the sign-count rule) is concrete.
- **Type/name consistency:** `WebAuthnCredential`, `webauthn_challenge`, `registration_options`/`verify_registration`/`authentication_options`/`verify_authentication`, `WebAuthnError`, `get_webauthn_config`/`is_configured`, `has_webauthn_credentials`, `LoginOut.methods`, audit actions `mfa.webauthn.add`/`remove` — used identically across tasks.
