# P2.1 — MASTER_KEY Rotation (key-versioning) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Allow rotating the Fernet `MASTER_KEY` without downtime or data loss: decrypt with any of an ordered key set, encrypt with the newest, and re-encrypt all stored secrets to the newest key so old keys can be retired.

**Architecture:** `crypto.py` switches from a single `Fernet` to `MultiFernet([primary, *old])` — encryption always uses the primary (first) key; decryption tries each key in order. `master_key` stays the primary (backward compatible); a new optional `master_key_old_keys` (comma-separated) holds decryption-only retired keys. A re-key script rotates every stored ciphertext to the primary key via `MultiFernet.rotate()`.

**Tech Stack:** cryptography (Fernet/MultiFernet), SQLAlchemy async, pydantic-settings, pytest.

**Test env (DB-backed tests):**
```
cd backend
export TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test"
export ADMIN_DATABASE_URL="$TEST_DATABASE_URL" DATABASE_URL="$TEST_DATABASE_URL"
export REDIS_URL="redis://localhost:6379" SESSION_SECRET="x"
export MASTER_KEY="$(./.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
```
Run: `./.venv/bin/python -m pytest tests/test_crypto.py -q`. Branch: `p2-master-key-rotation`.

**Encrypted data at rest (what the re-key must cover):**
- `devices.api_key_enc`, `devices.api_secret_enc` (bytea) — encrypted via `crypto.encrypt`.
- `config_snapshots.content_enc` (LargeBinary) — encrypted via `crypto.encrypt_bytes` (Fernet over gzipped config.xml).

---

## Task 1: MultiFernet crypto + old-keys config

**Files:**
- Modify: `backend/app/core/config.py`, `backend/app/core/crypto.py`
- Test: `backend/tests/test_crypto.py`

- [ ] **Step 1: Write the failing test**

Create/extend `backend/tests/test_crypto.py`:

```python
import importlib

from cryptography.fernet import Fernet

from app.core import config as config_mod
from app.core import crypto


def _reload_with(primary: str, old: str = ""):
    # Settings is lru_cached; rebuild it pointing at the given keys.
    config_mod.get_settings.cache_clear()
    import os
    os.environ["MASTER_KEY"] = primary
    if old:
        os.environ["MASTER_KEY_OLD_KEYS"] = old
    else:
        os.environ.pop("MASTER_KEY_OLD_KEYS", None)
    config_mod.get_settings.cache_clear()


def test_roundtrip_primary():
    k = Fernet.generate_key().decode()
    _reload_with(k)
    token = crypto.encrypt("secret")
    assert crypto.decrypt(token) == "secret"


def test_decrypts_with_old_key_after_rotation():
    old = Fernet.generate_key().decode()
    _reload_with(old)
    token = crypto.encrypt("secret")  # encrypted under the old key
    new = Fernet.generate_key().decode()
    _reload_with(new, old=old)        # new primary, old kept for decryption
    assert crypto.decrypt(token) == "secret"


def test_new_token_does_not_decrypt_with_only_old_key():
    old = Fernet.generate_key().decode()
    new = Fernet.generate_key().decode()
    _reload_with(new, old=old)
    token = crypto.encrypt("s")       # under new primary
    _reload_with(old)                 # only the old key present
    import pytest
    from cryptography.fernet import InvalidToken
    with pytest.raises(InvalidToken):
        crypto.decrypt(token)
```

- [ ] **Step 2: Run to verify it fails**

`cd backend && ./.venv/bin/python -m pytest tests/test_crypto.py -q` → FAIL (`MASTER_KEY_OLD_KEYS` unknown / single-key crypto).

- [ ] **Step 3: Add the config field**

In `backend/app/core/config.py`, after `master_key`:

```python
    master_key_old_keys: str = ""  # comma-separated retired Fernet keys, decryption-only (rotation)
```

- [ ] **Step 4: Switch crypto to MultiFernet**

Replace `backend/app/core/crypto.py`:

```python
from cryptography.fernet import Fernet, MultiFernet

from app.core.config import get_settings


def _multifernet() -> MultiFernet:
    settings = get_settings()
    keys = [Fernet(settings.master_key.encode())]
    keys += [
        Fernet(k.strip().encode())
        for k in settings.master_key_old_keys.split(",")
        if k.strip()
    ]
    return MultiFernet(keys)  # encrypts with keys[0] (primary); decrypts with any


def encrypt(plaintext: str) -> bytes:
    return _multifernet().encrypt(plaintext.encode())


def decrypt(ciphertext: bytes) -> str:
    return _multifernet().decrypt(bytes(ciphertext)).decode()


def encrypt_bytes(data: bytes) -> bytes:
    return _multifernet().encrypt(data)


def decrypt_bytes(token: bytes) -> bytes:
    return _multifernet().decrypt(bytes(token))


def rotate(token: bytes) -> bytes:
    """Re-encrypt an existing token under the primary key (decrypting with whichever key fits)."""
    return _multifernet().rotate(bytes(token))
```

- [ ] **Step 5: Run to verify it passes** — same command. Expected: PASS.

- [ ] **Step 6: Commit**

```
git add backend/app/core/config.py backend/app/core/crypto.py backend/tests/test_crypto.py
git commit -m "feat(crypto): MultiFernet with decryption-only old keys (key rotation)"
```

---

## Task 2: Re-key script

**Files:**
- Create: `backend/app/scripts/__init__.py` (if missing), `backend/app/scripts/rekey_secrets.py`
- Test: `backend/tests/test_rekey.py`

The script runs as the OWNER (uses `admin_database_url or database_url`, bypassing RLS) and rotates every stored ciphertext to the primary key. It is idempotent (rotating an already-primary token just re-wraps it).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_rekey.py`:

```python
import uuid

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import config as config_mod
from app.core import crypto
from app.scripts.rekey_secrets import rekey_all


@pytest.fixture
async def factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def test_rekey_reencrypts_device_secrets(factory):
    old = Fernet.generate_key().decode()
    new = Fernet.generate_key().decode()
    # Insert a device whose secrets are encrypted under the OLD key.
    config_mod.get_settings.cache_clear()
    import os
    os.environ["MASTER_KEY"] = old
    os.environ.pop("MASTER_KEY_OLD_KEYS", None)
    config_mod.get_settings.cache_clear()
    tid, did = uuid.uuid4(), uuid.uuid4()
    enc_key, enc_sec = crypto.encrypt("api-key"), crypto.encrypt("api-secret")
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'T','t','active')"), {"i": tid})
        await s.execute(
            text(
                "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags)"
                " VALUES (:i,:t,'d','https://d',:k,:s,true,'unverified','{}')"
            ),
            {"i": did, "t": tid, "k": enc_key, "s": enc_sec},
        )
        await s.commit()
    # Rotate: new primary, old kept for decryption.
    os.environ["MASTER_KEY"] = new
    os.environ["MASTER_KEY_OLD_KEYS"] = old
    config_mod.get_settings.cache_clear()
    n = await rekey_all(factory)
    assert n >= 1
    # Now the secrets must decrypt with the NEW key alone.
    os.environ["MASTER_KEY"] = new
    os.environ.pop("MASTER_KEY_OLD_KEYS", None)
    config_mod.get_settings.cache_clear()
    async with factory() as s:
        row = (await s.execute(text("SELECT api_key_enc, api_secret_enc FROM devices WHERE id=:i"), {"i": did})).one()
    assert crypto.decrypt(row.api_key_enc) == "api-key"
    assert crypto.decrypt(row.api_secret_enc) == "api-secret"
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_rekey.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement the script**

Create `backend/app/scripts/rekey_secrets.py`:

```python
"""Re-encrypt all stored secrets under the primary MASTER_KEY (key rotation).

Run AFTER setting the new key as MASTER_KEY and moving the previous key into
MASTER_KEY_OLD_KEYS, as the DB owner (RLS-exempt):

    python -m app.scripts.rekey_secrets

Then, once it succeeds, the retired key can be removed from MASTER_KEY_OLD_KEYS.
"""
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import crypto
from app.core.config import get_settings


async def rekey_all(factory) -> int:
    rotated = 0
    async with factory() as session:
        # devices: two bytea secret columns
        rows = (await session.execute(text("SELECT id, api_key_enc, api_secret_enc FROM devices"))).all()
        for r in rows:
            await session.execute(
                text("UPDATE devices SET api_key_enc=:k, api_secret_enc=:s WHERE id=:i"),
                {"i": r.id, "k": crypto.rotate(r.api_key_enc), "s": crypto.rotate(r.api_secret_enc)},
            )
            rotated += 1
        # config snapshots: one bytea blob column
        snaps = (await session.execute(text("SELECT id, content_enc FROM config_snapshots"))).all()
        for r in snaps:
            await session.execute(
                text("UPDATE config_snapshots SET content_enc=:c WHERE id=:i"),
                {"i": r.id, "c": crypto.rotate(r.content_enc)},
            )
            rotated += 1
        await session.commit()
    return rotated


def _owner_url() -> str:
    s = get_settings()
    return s.admin_database_url or s.database_url


async def _main() -> None:
    engine = create_async_engine(_owner_url(), pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    n = await rekey_all(factory)
    await engine.dispose()
    print(f"re-keyed {n} encrypted records")


if __name__ == "__main__":
    asyncio.run(_main())
```

Create `backend/app/scripts/__init__.py` if it does not exist (empty file).

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_rekey.py -q`. Expected: PASS.

- [ ] **Step 5: Commit**

```
git add backend/app/scripts/ backend/tests/test_rekey.py
git commit -m "feat(crypto): rekey_secrets script to re-encrypt under the primary key"
```

---

## Task 3: Docs — rotation procedure

**Files:** `backend/.env.example` (or root `.env.example`), `README.md`

- [ ] **Step 1: Document the env var**

Add `MASTER_KEY_OLD_KEYS=` (empty by default; comma-separated retired keys) to `.env.example` with a one-line comment.

- [ ] **Step 2: Document the rotation runbook in README**

Under the security/deployment notes, add a short "Rotating MASTER_KEY" runbook:
1. Generate a new key (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
2. Set it as `MASTER_KEY`; move the previous value into `MASTER_KEY_OLD_KEYS`. Deploy.
3. Run `python -m app.scripts.rekey_secrets` (in the api/worker image, as the owner).
4. Once it succeeds, remove the retired key from `MASTER_KEY_OLD_KEYS` and redeploy.

- [ ] **Step 3: Commit**

```
git add backend/.env.example README.md
git commit -m "docs: MASTER_KEY rotation runbook + MASTER_KEY_OLD_KEYS"
```

---

## Final verification
- [ ] `cd backend && ./.venv/bin/python -m pytest -q` all green (existing + new crypto/rekey tests).
- [ ] Open a PR to `main` (protected); merge once checks green (note: GitHub CodeQL incident may delay).
