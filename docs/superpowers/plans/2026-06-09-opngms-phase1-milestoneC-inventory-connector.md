# OPNGMS Phase 1 · Milestone C — Inventory, Secrets, Connector & Onboarding — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give OPNGMS the ability to manage OPNsense devices: encryption of secrets (Fernet/MASTER_KEY), the `OpnsenseClient` connector (single HTTP boundary, with error normalisation), the onboarding flow (connection test → status reachable|unverified), and the full device CRUD (create/list/get/update/delete + test-connection + rotate-secret) tenant-scoped — where the RLS from Milestone A is finally **exercised** by real queries, proven end-to-end through the API as a non-superuser role.

**Architecture:** API secrets (`api_key`/`api_secret`) are encrypted at-rest with **Fernet** (key from `MASTER_KEY`) and **never returned** to the client (write-only). A single **`OpnsenseClient`** abstraction (httpx async, HTTP Basic, TLS verification, timeout) is the only point that communicates with OPNsense; errors are normalised (`AuthError`/`ReachabilityError`/`ApiError`/`ParseError`). The onboarding **probe** is injected as a FastAPI dependency (overridable in tests with a fake → no real HTTP in endpoint tests; the real client is tested separately with **respx**). Device endpoints live under `/api/tenants/{tenant_id}/devices`, gated by `require_tenant` (DEVICE_VIEW/DEVICE_WRITE) and by `tenant_context` which sets `app.current_tenant` → RLS filters device queries. The app connects as `opngms_app` (non-superuser) in production, so RLS is effective; a dedicated test proves this through the API.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy 2.0 async, httpx, cryptography (Fernet), respx (test), Postgres, pytest.

---

## Spec reference
Implements sections **11 (onboarding), 12 (secrets), 13 (connector)** of the spec
`docs/superpowers/specs/2026-06-08-opngms-foundation-inventory-design.md`. Planning
decisions: connector **mocked** (respx) with OPNsense endpoints flagged "to verify
against a real device"; device scope **complete** (CRUD + test + rotate).

## Prerequisites (from Milestone A+B, in `main`)
- `Device` model (tenant_id, name, base_url, api_key_enc/api_secret_enc bytea, verify_tls,
  tls_fingerprint, site, tags, status, last_seen, firmware_version, created_at, updated_at).
- `DeviceRepository(session, tenant_id)` with `list()` + `add()` (app-layer scoping); RLS on
  `devices` (Milestone A); the app runs as `opngms_app` non-superuser.
- `app/core/deps.py`: `get_current_user`, `enforce_csrf`, `require_tenant(action)`,
  `tenant_context` (sets `app.current_tenant`). `app/core/rbac.py`: `Action.DEVICE_VIEW`,
  `Action.DEVICE_WRITE`. `app/services/audit.py`. `app/core/config.py` (`master_key`).
- conftest: `db_engine`, `api_client` (owner), factories. Test env (valid Fernet MASTER_KEY)
  already set in conftest.

NO new migrations: the `Device` model already has all required fields.

## File structure (created/modified)
```
backend/app/
  core/crypto.py              # NEW: encrypt/decrypt Fernet
  connectors/opnsense/
    __init__.py               # NEW
    client.py                 # NEW: OpnsenseClient + errors
  services/onboarding.py      # NEW: probe_device + ProbeResult + get_prober
  schemas/device.py           # NEW
  repositories/device.py      # MODIFY: get/update/delete
  api/devices.py              # NEW: tenant-scoped device router
  main.py                     # MODIFY: include devices_router
backend/tests/
  test_crypto.py
  test_opnsense_client.py     # respx
  test_onboarding.py
  test_devices_api.py
  test_devices_rls_api.py     # app_role_api_client (RLS end-to-end)
  test_c_integration.py
  conftest.py                 # MODIFY: app_role_api_client fixture
```

---

## Task 1: Secret encryption (Fernet)

**Files:** Create `backend/app/core/crypto.py`, `backend/tests/test_crypto.py`

- [ ] **Step 1: Failing test** — `backend/tests/test_crypto.py`:
```python
from app.core import crypto


def test_encrypt_decrypt_roundtrip():
    token = crypto.encrypt("api-secret-123")
    assert isinstance(token, bytes)
    assert token != b"api-secret-123"  # encrypted, not in plaintext
    assert crypto.decrypt(token) == "api-secret-123"


def test_two_encryptions_differ_but_both_decrypt():
    a = crypto.encrypt("x")
    b = crypto.encrypt("x")
    assert a != b  # Fernet includes timestamp+IV
    assert crypto.decrypt(a) == crypto.decrypt(b) == "x"
```
Run: `cd backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest tests/test_crypto.py -v` → FAIL (no module).

- [ ] **Step 2: Implement** — `backend/app/core/crypto.py`:
```python
from cryptography.fernet import Fernet

from app.core.config import get_settings


def _fernet() -> Fernet:
    return Fernet(get_settings().master_key.encode())


def encrypt(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode())


def decrypt(ciphertext: bytes) -> str:
    return _fernet().decrypt(bytes(ciphertext)).decode()
```
(`bytes(ciphertext)` tolerates SQLAlchemy returning a memoryview from a bytea column.)

- [ ] **Step 3: Run (PASS) + commit**
```bash
cd backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest tests/test_crypto.py -v
git add backend/app/core/crypto.py backend/tests/test_crypto.py
git commit -m "feat(backend): Fernet secret encryption (crypto.py)"
```
Expected: 2 passed.

---

## Task 2: OpnsenseClient + error normalisation (respx)

**Files:** Create `backend/app/connectors/opnsense/__init__.py`, `client.py`, `backend/tests/test_opnsense_client.py`

- [ ] **Step 1: Failing tests** — `backend/tests/test_opnsense_client.py`:
```python
import httpx
import pytest
import respx

from app.connectors.opnsense.client import (
    ApiError,
    AuthError,
    OpnsenseClient,
    ParseError,
    ReachabilityError,
)

BASE = "https://fw.test"
FW_URL = f"{BASE}/api/core/firmware/status"


@respx.mock
async def test_success_returns_version_and_sends_basic_auth():
    route = respx.get(FW_URL).mock(
        return_value=httpx.Response(200, json={"product_version": "24.1.1"})
    )
    client = OpnsenseClient(BASE, "key", "sec")
    version = await client.test_connection()
    assert version == "24.1.1"
    assert route.called
    assert route.calls.last.request.headers["authorization"].startswith("Basic ")


@respx.mock
async def test_401_raises_auth_error():
    respx.get(FW_URL).mock(return_value=httpx.Response(401))
    with pytest.raises(AuthError):
        await OpnsenseClient(BASE, "key", "bad").test_connection()


@respx.mock
async def test_timeout_raises_reachability_error():
    respx.get(FW_URL).mock(side_effect=httpx.ConnectTimeout("timeout"))
    with pytest.raises(ReachabilityError):
        await OpnsenseClient(BASE, "key", "sec").test_connection()


@respx.mock
async def test_500_raises_api_error_with_status():
    respx.get(FW_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(ApiError) as ei:
        await OpnsenseClient(BASE, "key", "sec").test_connection()
    assert ei.value.status_code == 503


@respx.mock
async def test_non_json_raises_parse_error():
    respx.get(FW_URL).mock(return_value=httpx.Response(200, text="not json"))
    with pytest.raises(ParseError):
        await OpnsenseClient(BASE, "key", "sec").test_connection()
```
Run → FAIL (no module).

- [ ] **Step 2: Implement** — `backend/app/connectors/opnsense/__init__.py`: empty.
`backend/app/connectors/opnsense/client.py`:
```python
import httpx


class OpnsenseError(Exception):
    """Base class for OPNsense connector errors."""


class AuthError(OpnsenseError):
    """API credentials rejected (401/403)."""


class ReachabilityError(OpnsenseError):
    """Device unreachable (DNS/TLS/connection/timeout)."""


class ApiError(OpnsenseError):
    """HTTP error response (4xx/5xx non-auth)."""

    def __init__(self, status_code: int, message: str = "") -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {message}")


class ParseError(OpnsenseError):
    """Response cannot be parsed as JSON."""


class OpnsenseClient:
    """Single HTTP boundary to an OPNsense device.

    HTTP Basic auth (api_key as username, api_secret as password) over HTTPS.
    NOTE: exact endpoints are TO BE VERIFIED against a real OPNsense device; here
    `core/firmware/status` is used for connection test + firmware version.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_secret: str,
        *,
        verify_tls: bool = True,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = (api_key, api_secret)
        self._verify = verify_tls
        self._timeout = timeout

    async def _get(self, path: str) -> dict:
        url = f"{self._base_url}/api/{path.lstrip('/')}"
        try:
            async with httpx.AsyncClient(
                verify=self._verify, timeout=self._timeout, auth=self._auth
            ) as client:
                resp = await client.get(url)
        except httpx.HTTPError as exc:  # ConnectError/Timeout/TLS/etc.
            raise ReachabilityError(str(exc)) from exc
        if resp.status_code in (401, 403):
            raise AuthError(f"auth failed: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, resp.text[:200])
        try:
            return resp.json()
        except ValueError as exc:
            raise ParseError(str(exc)) from exc

    async def get_firmware_status(self) -> dict:
        return await self._get("core/firmware/status")

    async def test_connection(self) -> str | None:
        """Check reachability+credentials; returns firmware version or None.

        Raises AuthError/ReachabilityError/ApiError/ParseError on failure.
        """
        data = await self.get_firmware_status()
        # Field TO BE VERIFIED on a real OPNsense device (exact name may differ).
        version = data.get("product_version")
        if version is None and isinstance(data.get("product"), dict):
            version = data["product"].get("product_version")
        return version
```

- [ ] **Step 3: Run (PASS) + commit**
```bash
cd backend && .venv/bin/python -m pytest tests/test_opnsense_client.py -v
git add backend/app/connectors backend/tests/test_opnsense_client.py
git commit -m "feat(backend): OpnsenseClient + error normalisation (respx)"
```
Expected: 5 passed. (respx mocks httpx; no DB needed.)

---

## Task 3: Device schemas

**Files:** Create `backend/app/schemas/device.py`. Verify import.

- [ ] **Step 1: Implement** — `backend/app/schemas/device.py`:
```python
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class DeviceIn(BaseModel):
    name: str
    base_url: str
    api_key: str
    api_secret: str
    verify_tls: bool = True
    tls_fingerprint: str | None = None
    site: str | None = None
    tags: list[str] = Field(default_factory=list)


class DeviceUpdateIn(BaseModel):
    name: str | None = None
    base_url: str | None = None
    verify_tls: bool | None = None
    tls_fingerprint: str | None = None
    site: str | None = None
    tags: list[str] | None = None


class RotateSecretIn(BaseModel):
    api_key: str
    api_secret: str


class DeviceOut(BaseModel):
    # NB: NO secret fields (api_key_enc/api_secret_enc) — write-only.
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    base_url: str
    verify_tls: bool
    tls_fingerprint: str | None
    site: str | None
    tags: list[str]
    status: str
    last_seen: datetime | None
    firmware_version: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TestResultOut(BaseModel):
    status: str  # reachable | unverified
    firmware_version: str | None = None
    error: str | None = None
```

- [ ] **Step 2: Verify + commit**
```bash
cd backend && .venv/bin/python -c "import app.schemas.device; print('ok')"
git add backend/app/schemas/device.py
git commit -m "feat(backend): device schemas (DeviceOut without secrets)"
```

---

## Task 4: DeviceRepository — get/update/delete

**Files:** Modify `backend/app/repositories/device.py`

- [ ] **Step 1: Add methods** (keep existing `list`/`add`; read the file first). Add:
```python
    async def get(self, device_id: uuid.UUID) -> Device | None:
        result = await self.session.execute(
            select(Device).where(
                Device.id == device_id, Device.tenant_id == self.tenant_id
            )
        )
        return result.scalar_one_or_none()

    async def delete(self, device: Device) -> None:
        await self.session.delete(device)
        await self.session.flush()
```
(`select` already imported. The `get` keeps app-layer tenant scoping — defense-in-depth with RLS.)

- [ ] **Step 2: Verify import + full suite + commit**
```bash
cd backend && .venv/bin/python -c "from app.repositories.device import DeviceRepository; print('ok')"
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest -q
git add backend/app/repositories/device.py
git commit -m "feat(backend): DeviceRepository get/update(delete)"
```
Expected: suite still green (no behavior change for existing tests).

---

## Task 5: Onboarding — probe_device + overridable dependency

**Files:** Create `backend/app/services/onboarding.py`, `backend/tests/test_onboarding.py`

- [ ] **Step 1: Failing tests** — `backend/tests/test_onboarding.py`:
```python
import httpx
import respx

from app.services.onboarding import probe_device

BASE = "https://fw.test"
FW_URL = f"{BASE}/api/core/firmware/status"


@respx.mock
async def test_probe_success_reachable_with_version():
    respx.get(FW_URL).mock(
        return_value=httpx.Response(200, json={"product_version": "24.7"})
    )
    result = await probe_device(BASE, "key", "sec", verify_tls=True)
    assert result.reachable is True
    assert result.firmware_version == "24.7"
    assert result.error is None


@respx.mock
async def test_probe_failure_unverified_with_error():
    respx.get(FW_URL).mock(return_value=httpx.Response(401))
    result = await probe_device(BASE, "key", "bad", verify_tls=True)
    assert result.reachable is False
    assert result.firmware_version is None
    assert "AuthError" in result.error
```
Run → FAIL (no module).

- [ ] **Step 2: Implement** — `backend/app/services/onboarding.py`:
```python
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from app.connectors.opnsense.client import OpnsenseClient, OpnsenseError


@dataclass
class ProbeResult:
    reachable: bool
    firmware_version: str | None
    error: str | None


async def probe_device(
    base_url: str,
    api_key: str,
    api_secret: str,
    *,
    verify_tls: bool = True,
    tls_fingerprint: str | None = None,
) -> ProbeResult:
    client = OpnsenseClient(base_url, api_key, api_secret, verify_tls=verify_tls)
    try:
        version = await client.test_connection()
        return ProbeResult(reachable=True, firmware_version=version, error=None)
    except OpnsenseError as exc:
        return ProbeResult(
            reachable=False, firmware_version=None, error=f"{type(exc).__name__}: {exc}"
        )


# Injectable "prober" type (overridable in endpoint tests).
Prober = Callable[..., Coroutine[Any, Any, ProbeResult]]


def get_prober() -> Prober:
    return probe_device
```

- [ ] **Step 3: Run (PASS) + commit**
```bash
cd backend && .venv/bin/python -m pytest tests/test_onboarding.py -v
git add backend/app/services/onboarding.py backend/tests/test_onboarding.py
git commit -m "feat(backend): onboarding probe_device + get_prober dependency"
```
Expected: 2 passed.

---

## Task 6: Device API — create (onboarding) + list + get

**Files:** Create `backend/app/api/devices.py`; modify `backend/app/main.py`; `backend/tests/test_devices_api.py`

- [ ] **Step 1: Failing tests** — `backend/tests/test_devices_api.py`:
```python
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.device import Device
from tests.factories import make_membership, make_tenant, make_user

CSRF = {"X-OPNGMS-CSRF": "1"}


async def _seed_admin_member(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        admin = await make_user(s, email="ta@x.io", password="pw12345")
        await make_membership(s, user_id=admin.id, tenant_id=t.id, role="tenant_admin")
        viewer = await make_user(s, email="ro@x.io", password="pw12345")
        await make_membership(s, user_id=viewer.id, tenant_id=t.id, role="read_only")
        await s.commit()
        return t.id


def _fake_prober_factory(app, reachable=True, version="24.7", error=None):
    from app.services.onboarding import ProbeResult, get_prober

    async def _fake(*args, **kwargs):
        return ProbeResult(reachable=reachable, firmware_version=version, error=error)

    app.dependency_overrides[get_prober] = lambda: _fake


async def test_create_device_reachable_and_secrets_hidden(api_client, db_engine):
    from app.main import app

    tenant_id = await _seed_admin_member(db_engine)
    _fake_prober_factory(app, reachable=True, version="24.7")
    await api_client.post("/api/login", json={"email": "ta@x.io", "password": "pw12345"})
    resp = await api_client.post(
        f"/api/tenants/{tenant_id}/devices",
        json={"name": "fw1", "base_url": "https://fw1", "api_key": "k", "api_secret": "s"},
        headers=CSRF,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "reachable"
    assert body["firmware_version"] == "24.7"
    assert "api_key" not in body and "api_secret" not in body
    assert "api_key_enc" not in body and "api_secret_enc" not in body
    from app.services.onboarding import get_prober
    app.dependency_overrides.pop(get_prober, None)


async def test_create_device_unverified_when_probe_fails(api_client, db_engine):
    from app.main import app

    tenant_id = await _seed_admin_member(db_engine)
    _fake_prober_factory(app, reachable=False, version=None, error="AuthError: x")
    await api_client.post("/api/login", json={"email": "ta@x.io", "password": "pw12345"})
    resp = await api_client.post(
        f"/api/tenants/{tenant_id}/devices",
        json={"name": "fw2", "base_url": "https://fw2", "api_key": "k", "api_secret": "bad"},
        headers=CSRF,
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "unverified"
    from app.services.onboarding import get_prober
    app.dependency_overrides.pop(get_prober, None)


async def test_secrets_encrypted_at_rest(api_client, db_engine):
    from app.main import app

    tenant_id = await _seed_admin_member(db_engine)
    _fake_prober_factory(app)
    await api_client.post("/api/login", json={"email": "ta@x.io", "password": "pw12345"})
    await api_client.post(
        f"/api/tenants/{tenant_id}/devices",
        json={"name": "fw3", "base_url": "https://fw3", "api_key": "the-key", "api_secret": "the-secret"},
        headers=CSRF,
    )
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        row = (await s.execute(select(Device).where(Device.name == "fw3"))).scalar_one()
        assert bytes(row.api_secret_enc) != b"the-secret"  # encrypted
        from app.core import crypto
        assert crypto.decrypt(row.api_secret_enc) == "the-secret"  # decryptable
    from app.services.onboarding import get_prober
    app.dependency_overrides.pop(get_prober, None)


async def test_read_only_can_list_but_not_create(api_client, db_engine):
    from app.main import app
    from app.services.onboarding import get_prober

    tenant_id = await _seed_admin_member(db_engine)
    _fake_prober_factory(app)
    await api_client.post("/api/login", json={"email": "ro@x.io", "password": "pw12345"})
    listed = await api_client.get(f"/api/tenants/{tenant_id}/devices")
    assert listed.status_code == 200
    denied = await api_client.post(
        f"/api/tenants/{tenant_id}/devices",
        json={"name": "x", "base_url": "https://x", "api_key": "k", "api_secret": "s"},
        headers=CSRF,
    )
    assert denied.status_code == 403
    app.dependency_overrides.pop(get_prober, None)
```

- [ ] **Step 2: Implement the router** — `backend/app/api/devices.py`:
```python
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.db import get_session
from app.core.deps import TenantContext, enforce_csrf, require_tenant
from app.core.rbac import Action
from app.models.device import Device
from app.repositories.device import DeviceRepository
from app.schemas.device import DeviceIn, DeviceOut
from app.services.audit import AuditService
from app.services.onboarding import Prober, get_prober

router = APIRouter(prefix="/api/tenants/{tenant_id}/devices", tags=["devices"])


@router.get("", response_model=list[DeviceOut])
async def list_devices(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> list[Device]:
    return list(await DeviceRepository(session, tenant_id).list())


@router.get("/{device_id}", response_model=DeviceOut)
async def get_device(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> Device:
    device = await DeviceRepository(session, tenant_id).get(device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return device


@router.post(
    "",
    response_model=DeviceOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_csrf)],
)
async def create_device(
    tenant_id: uuid.UUID,
    payload: DeviceIn,
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_WRITE)),
    session: AsyncSession = Depends(get_session),
    prober: Prober = Depends(get_prober),
) -> Device:
    result = await prober(
        payload.base_url,
        payload.api_key,
        payload.api_secret,
        verify_tls=payload.verify_tls,
        tls_fingerprint=payload.tls_fingerprint,
    )
    device = Device(
        name=payload.name,
        base_url=payload.base_url,
        api_key_enc=crypto.encrypt(payload.api_key),
        api_secret_enc=crypto.encrypt(payload.api_secret),
        verify_tls=payload.verify_tls,
        tls_fingerprint=payload.tls_fingerprint,
        site=payload.site,
        tags=payload.tags,
        status="reachable" if result.reachable else "unverified",
        firmware_version=result.firmware_version,
        last_seen=datetime.now(timezone.utc) if result.reachable else None,
    )
    device = await DeviceRepository(session, tenant_id).add(device)  # add() sets tenant_id
    await AuditService(session).record(
        actor_user_id=ctx.user.id,
        tenant_id=tenant_id,
        action="device.create",
        target_type="device",
        target_id=str(device.id),
        ip=request.client.host if request.client else None,
        details={"name": device.name, "status": device.status},
    )
    await session.commit()
    return device
```

- [ ] **Step 3: Mount** in `backend/app/main.py`:
```python
from app.api.devices import router as devices_router

app.include_router(devices_router)
```

- [ ] **Step 4: Run tests + full suite + commit**
```bash
cd backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest tests/test_devices_api.py -v
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest -q
git add backend/app/api/devices.py backend/app/main.py backend/tests/test_devices_api.py
git commit -m "feat(backend): device API create(onboarding)/list/get + write-only secrets + audit"
```
Expected: device tests PASS (4); full suite green.

---

## Task 7: Device API — update + delete

**Files:** Modify `backend/app/api/devices.py`; `backend/tests/test_devices_update_delete.py`

- [ ] **Step 1: Failing tests** — `backend/tests/test_devices_update_delete.py`:
```python
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.factories import make_membership, make_tenant, make_user

CSRF = {"X-OPNGMS-CSRF": "1"}


async def _seed_and_login(api_client, db_engine):
    from app.main import app
    from app.services.onboarding import ProbeResult, get_prober

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        admin = await make_user(s, email="ta@x.io", password="pw12345")
        await make_membership(s, user_id=admin.id, tenant_id=t.id, role="tenant_admin")
        await s.commit()
        tenant_id = t.id

    async def _fake(*a, **k):
        return ProbeResult(reachable=True, firmware_version="24.7", error=None)

    app.dependency_overrides[get_prober] = lambda: _fake
    await api_client.post("/api/login", json={"email": "ta@x.io", "password": "pw12345"})
    return tenant_id


async def _create(api_client, tenant_id, name="fw1"):
    r = await api_client.post(
        f"/api/tenants/{tenant_id}/devices",
        json={"name": name, "base_url": "https://fw1", "api_key": "k", "api_secret": "s"},
        headers=CSRF,
    )
    return r.json()["id"]


async def test_update_device_fields(api_client, db_engine):
    from app.main import app
    from app.services.onboarding import get_prober

    tenant_id = await _seed_and_login(api_client, db_engine)
    device_id = await _create(api_client, tenant_id)
    resp = await api_client.patch(
        f"/api/tenants/{tenant_id}/devices/{device_id}",
        json={"name": "fw1-renamed", "tags": ["edge"]},
        headers=CSRF,
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "fw1-renamed"
    assert resp.json()["tags"] == ["edge"]
    app.dependency_overrides.pop(get_prober, None)


async def test_delete_device(api_client, db_engine):
    from app.main import app
    from app.services.onboarding import get_prober

    tenant_id = await _seed_and_login(api_client, db_engine)
    device_id = await _create(api_client, tenant_id)
    d = await api_client.delete(f"/api/tenants/{tenant_id}/devices/{device_id}", headers=CSRF)
    assert d.status_code == 204
    g = await api_client.get(f"/api/tenants/{tenant_id}/devices/{device_id}")
    assert g.status_code == 404
    app.dependency_overrides.pop(get_prober, None)
```

- [ ] **Step 2: Add update + delete handlers** to `backend/app/api/devices.py` (append; import `DeviceUpdateIn`):
```python
from app.schemas.device import DeviceUpdateIn  # add to imports


@router.patch("/{device_id}", response_model=DeviceOut, dependencies=[Depends(enforce_csrf)])
async def update_device(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    payload: DeviceUpdateIn,
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> Device:
    repo = DeviceRepository(session, tenant_id)
    device = await repo.get(device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(device, field, value)
    await session.flush()
    await AuditService(session).record(
        actor_user_id=ctx.user.id,
        tenant_id=tenant_id,
        action="device.update",
        target_type="device",
        target_id=str(device.id),
        ip=request.client.host if request.client else None,
        details=payload.model_dump(exclude_unset=True),
    )
    await session.commit()
    return device


@router.delete(
    "/{device_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(enforce_csrf)],
)
async def delete_device(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> None:
    repo = DeviceRepository(session, tenant_id)
    device = await repo.get(device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    await repo.delete(device)
    await AuditService(session).record(
        actor_user_id=ctx.user.id,
        tenant_id=tenant_id,
        action="device.delete",
        target_type="device",
        target_id=str(device_id),
        ip=request.client.host if request.client else None,
        details={},
    )
    await session.commit()
```

- [ ] **Step 3: Run + commit**
```bash
cd backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest tests/test_devices_update_delete.py -v
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest -q
git add backend/app/api/devices.py backend/tests/test_devices_update_delete.py
git commit -m "feat(backend): device API update/delete + audit"
```
Expected: 2 passed; full suite green.

---

## Task 8: Device API — test-connection + rotate-secret

**Files:** Modify `backend/app/api/devices.py`; `backend/tests/test_devices_test_rotate.py`

- [ ] **Step 1: Failing tests** — `backend/tests/test_devices_test_rotate.py`:
```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.device import Device
from tests.factories import make_membership, make_tenant, make_user

CSRF = {"X-OPNGMS-CSRF": "1"}


async def _seed_login(api_client, db_engine, reachable=True):
    from app.main import app
    from app.services.onboarding import ProbeResult, get_prober

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        admin = await make_user(s, email="ta@x.io", password="pw12345")
        await make_membership(s, user_id=admin.id, tenant_id=t.id, role="tenant_admin")
        await s.commit()
        tenant_id = t.id

    async def _fake(*a, **k):
        return ProbeResult(reachable=reachable, firmware_version="24.7" if reachable else None, error=None if reachable else "AuthError: x")

    app.dependency_overrides[get_prober] = lambda: _fake
    await api_client.post("/api/login", json={"email": "ta@x.io", "password": "pw12345"})
    return tenant_id


async def _create(api_client, tenant_id):
    r = await api_client.post(
        f"/api/tenants/{tenant_id}/devices",
        json={"name": "fw1", "base_url": "https://fw1", "api_key": "k0", "api_secret": "s0"},
        headers=CSRF,
    )
    return r.json()["id"]


async def test_test_connection_endpoint(api_client, db_engine):
    from app.main import app
    from app.services.onboarding import get_prober

    tenant_id = await _seed_login(api_client, db_engine, reachable=True)
    device_id = await _create(api_client, tenant_id)
    resp = await api_client.post(
        f"/api/tenants/{tenant_id}/devices/{device_id}/test-connection", headers=CSRF
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "reachable"
    assert resp.json()["firmware_version"] == "24.7"
    app.dependency_overrides.pop(get_prober, None)


async def test_rotate_secret_changes_ciphertext(api_client, db_engine):
    from app.core import crypto
    from app.main import app
    from app.services.onboarding import get_prober

    tenant_id = await _seed_login(api_client, db_engine, reachable=True)
    device_id = await _create(api_client, tenant_id)
    resp = await api_client.post(
        f"/api/tenants/{tenant_id}/devices/{device_id}/rotate-secret",
        json={"api_key": "k1", "api_secret": "s1"},
        headers=CSRF,
    )
    assert resp.status_code == 200
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        row = (await s.execute(select(Device).where(Device.id == device_id))).scalar_one()
        assert crypto.decrypt(row.api_key_enc) == "k1"
        assert crypto.decrypt(row.api_secret_enc) == "s1"
    app.dependency_overrides.pop(get_prober, None)
```

- [ ] **Step 2: Add handlers** to `backend/app/api/devices.py` (append; import `RotateSecretIn`, `TestResultOut`):
```python
from app.schemas.device import RotateSecretIn, TestResultOut  # add to imports


@router.post(
    "/{device_id}/test-connection",
    response_model=TestResultOut,
    dependencies=[Depends(enforce_csrf)],
)
async def test_device_connection(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_WRITE)),
    session: AsyncSession = Depends(get_session),
    prober: Prober = Depends(get_prober),
) -> TestResultOut:
    repo = DeviceRepository(session, tenant_id)
    device = await repo.get(device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    result = await prober(
        device.base_url,
        crypto.decrypt(device.api_key_enc),
        crypto.decrypt(device.api_secret_enc),
        verify_tls=device.verify_tls,
        tls_fingerprint=device.tls_fingerprint,
    )
    device.status = "reachable" if result.reachable else "unverified"
    if result.reachable:
        device.last_seen = datetime.now(timezone.utc)
        device.firmware_version = result.firmware_version
    await session.flush()
    await AuditService(session).record(
        actor_user_id=ctx.user.id,
        tenant_id=tenant_id,
        action="device.test",
        target_type="device",
        target_id=str(device.id),
        ip=request.client.host if request.client else None,
        details={"status": device.status},
    )
    await session.commit()
    return TestResultOut(
        status=device.status, firmware_version=device.firmware_version, error=result.error
    )


@router.post(
    "/{device_id}/rotate-secret",
    response_model=DeviceOut,
    dependencies=[Depends(enforce_csrf)],
)
async def rotate_secret(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    payload: RotateSecretIn,
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> Device:
    repo = DeviceRepository(session, tenant_id)
    device = await repo.get(device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    device.api_key_enc = crypto.encrypt(payload.api_key)
    device.api_secret_enc = crypto.encrypt(payload.api_secret)
    await session.flush()
    await AuditService(session).record(
        actor_user_id=ctx.user.id,
        tenant_id=tenant_id,
        action="device.rotate_secret",
        target_type="device",
        target_id=str(device.id),
        ip=request.client.host if request.client else None,
        details={},  # NEVER log secrets
    )
    await session.commit()
    return device
```

- [ ] **Step 3: Run + commit**
```bash
cd backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest tests/test_devices_test_rotate.py -v
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest -q
git add backend/app/api/devices.py backend/tests/test_devices_test_rotate.py
git commit -m "feat(backend): device API test-connection + rotate-secret + audit"
```
Expected: 2 passed; full suite green.

---

## Task 9: Device RLS end-to-end via API (non-superuser role)

**Files:** Modify `backend/tests/conftest.py` (add `app_role_api_client`); create `backend/tests/test_devices_rls_api.py`

This proves that RLS on devices holds THROUGH the API when the app connects as
`opngms_app` (non-superuser), not just at the application layer.

- [ ] **Step 1: Add the `app_role_api_client` fixture** to `backend/tests/conftest.py`:
```python
from sqlalchemy.engine import make_url


@pytest.fixture
async def app_role_api_client(db_engine):
    """Like api_client, but the session connects as opngms_app (non-superuser) -> RLS active."""
    app_url = make_url(TEST_DB_URL).set(username="opngms_app", password="opngms_app")
    engine = make_engine(app_url.render_as_string(hide_password=False))
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_session():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()
```
(Reuse existing imports; add `make_url`. `db_engine` already creates schema+RLS+role as owner.)

- [ ] **Step 2: Failing test** — `backend/tests/test_devices_rls_api.py`:
```python
from app.services.onboarding import ProbeResult, get_prober

CSRF = {"X-OPNGMS-CSRF": "1"}


async def _setup_two_tenants(app_role_api_client, db_engine):
    from app.main import app
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from tests.factories import make_tenant

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        a = await make_tenant(s, slug="a")
        b = await make_tenant(s, slug="b")
        await s.commit()
        ta, tb = a.id, b.id
    # superadmin via /api/setup (can access all tenants)
    await app_role_api_client.post(
        "/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345"}
    )

    async def _fake(*ar, **kw):
        return ProbeResult(reachable=True, firmware_version="24.7", error=None)

    app.dependency_overrides[get_prober] = lambda: _fake
    await app_role_api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345"})
    return ta, tb


async def test_device_created_in_tenant_a_not_visible_in_tenant_b(app_role_api_client, db_engine):
    from app.main import app

    ta, tb = await _setup_two_tenants(app_role_api_client, db_engine)
    # create a device in tenant A (context A, RLS WITH CHECK ok)
    created = await app_role_api_client.post(
        f"/api/tenants/{ta}/devices",
        json={"name": "fw-a", "base_url": "https://a", "api_key": "k", "api_secret": "s"},
        headers=CSRF,
    )
    assert created.status_code == 201
    # list in tenant A: device is visible
    la = await app_role_api_client.get(f"/api/tenants/{ta}/devices")
    assert [d["name"] for d in la.json()] == ["fw-a"]
    # list in tenant B: RLS (context B) does not show tenant A's device
    lb = await app_role_api_client.get(f"/api/tenants/{tb}/devices")
    assert lb.json() == []
    app.dependency_overrides.pop(get_prober, None)
```

- [ ] **Step 3: Run + full suite + commit**
```bash
cd backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest tests/test_devices_rls_api.py -v
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest -q
git add backend/tests/conftest.py backend/tests/test_devices_rls_api.py
git commit -m "test(backend): device RLS isolation end-to-end via API (opngms_app)"
```
Expected: RLS API test PASS; full suite green.

---

## Task 10: e2e integration + final suite

**Files:** Create `backend/tests/test_c_integration.py`

- [ ] **Step 1: Integration test** — `backend/tests/test_c_integration.py`:
```python
from app.services.onboarding import ProbeResult, get_prober

CSRF = {"X-OPNGMS-CSRF": "1"}


async def test_device_lifecycle(api_client, db_engine):
    from app.main import app
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from tests.factories import make_membership, make_tenant, make_user

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        op = await make_user(s, email="op@x.io", password="pw12345")
        await make_membership(s, user_id=op.id, tenant_id=t.id, role="operator")
        await s.commit()
        tenant_id = t.id

    async def _fake(*a, **k):
        return ProbeResult(reachable=True, firmware_version="24.7", error=None)

    app.dependency_overrides[get_prober] = lambda: _fake
    await api_client.post("/api/login", json={"email": "op@x.io", "password": "pw12345"})

    # create -> reachable
    c = await api_client.post(
        f"/api/tenants/{tenant_id}/devices",
        json={"name": "fw", "base_url": "https://fw", "api_key": "k", "api_secret": "s"},
        headers=CSRF,
    )
    assert c.status_code == 201
    did = c.json()["id"]
    # get
    assert (await api_client.get(f"/api/tenants/{tenant_id}/devices/{did}")).status_code == 200
    # update
    u = await api_client.patch(
        f"/api/tenants/{tenant_id}/devices/{did}", json={"site": "HQ"}, headers=CSRF
    )
    assert u.json()["site"] == "HQ"
    # rotate
    assert (await api_client.post(
        f"/api/tenants/{tenant_id}/devices/{did}/rotate-secret",
        json={"api_key": "k2", "api_secret": "s2"}, headers=CSRF,
    )).status_code == 200
    # test-connection
    assert (await api_client.post(
        f"/api/tenants/{tenant_id}/devices/{did}/test-connection", headers=CSRF
    )).json()["status"] == "reachable"
    # delete
    assert (await api_client.delete(
        f"/api/tenants/{tenant_id}/devices/{did}", headers=CSRF
    )).status_code == 204
    app.dependency_overrides.pop(get_prober, None)
```

- [ ] **Step 2: Run whole suite + commit**
```bash
cd backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest -q
git add backend/tests/test_c_integration.py
git commit -m "test(backend): Milestone C end-to-end integration (device lifecycle)"
```
Expected: full suite green.

---

## Self-review (spec → task mapping)
- **Spec §12 Secrets** (Fernet, write-only) → Task 1 (crypto), Task 6 (DeviceOut without secrets,
  encrypted-at-rest test), Task 8 (rotate, secrets never in audit).
- **Spec §13 Connector** (`OpnsenseClient`, single boundary, error normalisation) → Task 2
  (+respx). HTTP Basic, verify TLS, timeout. Exact endpoints flagged "to verify".
- **Spec §11 Onboarding** (test → reachable|unverified, precise error) → Task 5 (probe), Task 6
  (create saves with status; failure → unverified). Saves even if the test fails.
- **RLS wiring exercised** → Task 9: device API as `opngms_app`, cross-tenant isolation
  proven end-to-end through `tenant_context` (`app.current_tenant`) + RLS.
- **Device RBAC** → Task 6/7/8 (`require_tenant(DEVICE_VIEW|DEVICE_WRITE)`): read_only can view
  but not write (test in Task 6).

**Scope notes / tracked debt:**
- **TLS fingerprint pinning** NOT applied by the connector in this milestone (the
  `tls_fingerprint` field is accepted but `verify_tls` remains bool: True=CA verification,
  False=no verification). For the self-signed certs common on OPNsense, `verify_tls=False`
  = MITM risk → actual fingerprint pinning is **future debt** (to be documented).
- OPNsense endpoints (`core/firmware/status`, field `product_version`) **to be verified** against
  a real device; the abstraction and tests (mock) remain unchanged.
- Guard transaction-scoping of `set_tenant_context` (from Milestone B debt): device queries
  are executed in the same transaction where the context is set (no mid-handler commit before
  the query) — respected in all endpoints in this plan.

**Placeholder scan:** no TBD/TODO; every step has concrete code/commands.
**Type consistency:** `crypto.encrypt/decrypt`, `OpnsenseClient`, `ProbeResult(reachable,
firmware_version,error)`, `get_prober`/`Prober`, `DeviceRepository(session,tenant_id)`
.get/.add/.delete, `require_tenant(Action.DEVICE_*)`, `DeviceOut` (no secrets) consistent across
Tasks 1-10.

---

## Technical debt (from final holistic review — READY TO MERGE)

Zero Critical/Important issues. Write-only secret management verified end-to-end, device RLS
exercised via `opngms_app` non-superuser connection, correct RBAC. To track:

1. ⚠️ **TLS fingerprint pinning not applied** (highest priority). `verify_tls=False` (common
   case for OPNsense self-signed certs) = no certificate verification → MITM risk.
   The `tls_fingerprint` field is stored but ignored by the connector. Implement real pinning
   (custom httpx SSL context) before production.
2. **OPNsense endpoints to verify** against a real device (`core/firmware/status`, field
   `product_version`) — mocked today.
3. **No re-probe on `base_url` change** (PATCH): `status`/`firmware_version`/`last_seen`
   remain stale until test-connection is triggered.
4. **Connector without retry/backoff** or shared HTTP session / per-device concurrency limits
   (spec §13, needed for Phase 2 polling). Today a new `AsyncClient` per request.
5. **No pagination** on `GET /devices`.
6. **No MASTER_KEY rotation / key versioning**: rotating the key would make existing
   ciphertexts undecryptable (missing a key-id column + re-encryption path).
7. **App role password hardcoded** (`opngms_app/opngms_app`, MVP default) — change and
   update `DATABASE_URL` in production.
