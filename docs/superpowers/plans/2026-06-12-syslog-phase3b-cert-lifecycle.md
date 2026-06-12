# Syslog Phase 3.2 — Certificate Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Operator-driven certificate **rotation** + **soft revocation** (with a CRL-ready revocation ledger) for per-device mTLS log forwarding, from the device "Log forwarding" card.

**Architecture:** Two new tenant-scoped service functions on `log_forwarding.py` reuse only Phase-1-verified OPNsense primitives (import_cert / add+delete syslog destination / delete_cert). Rotation adds the new destination before deleting the old (no log gap). Revocation snapshots the cert serial into a new RLS `revoked_syslog_certs` ledger and marks the row revoked, all in one box-gated transaction. Two `CONFIG_PUSH` endpoints + two card buttons.

**Tech Stack:** Python 3.14 · FastAPI · SQLAlchemy + Alembic + Postgres RLS · React 19 + Mantine v9 + react-query + openapi-fetch · pytest · vitest + MSW.

**Spec:** `docs/superpowers/specs/2026-06-12-syslog-phase3b-cert-lifecycle-design.md`
**Branch:** `feat/log-forwarding-cert-lifecycle` (already created off main).

---

## File Structure

**Backend — create:** `app/models/revoked_syslog_cert.py`, `backend/migrations/versions/0026_cert_lifecycle.py`, tests `tests/test_cert_lifecycle_model.py`, `tests/test_cert_lifecycle_service.py`, `tests/test_cert_lifecycle_api.py`.
**Backend — modify:** `app/core/rls.py` (TENANT_TABLES), `app/models/__init__.py` (register model), `app/models/device_log_forwarding.py` (`revoked_at`), `app/services/log_forwarding.py` (`rotate_device_cert`, `revoke_device`, clear `revoked_at` on provision), `app/schemas/log_forwarding.py` (`revoked_at` + `RevokeIn`), `app/api/log_forwarding.py` (2 endpoints + `_out`).
**Frontend — create:** `frontend/src/components/__tests__/certLifecycle.test.tsx`.
**Frontend — modify:** `frontend/src/api/schema.d.ts` + `frontend/openapi.json` (regen), `frontend/src/logs/logForwardingHooks.ts` (rotate/revoke), `frontend/src/components/LogForwardingCard.tsx` (buttons + Revoked badge).

---

## Conventions
- Backend DB tests prefix: `TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test"`. Pure tests always run. The test harness builds the schema via `Base.metadata.create_all` (conftest), so a NEW table needs its model imported in `app/models/__init__.py`.
- Commit from REPO ROOT with `backend/...`/`frontend/...` paths. English everywhere; commit after each task. Frontend PR gate: `npm run build`.

---

# PHASE A — backend

## Task 1: Revocation ledger model + migration + state column

**Files:**
- Create: `backend/app/models/revoked_syslog_cert.py`, `backend/migrations/versions/0026_cert_lifecycle.py`, `backend/tests/test_cert_lifecycle_model.py`
- Modify: `backend/app/core/rls.py`, `backend/app/models/__init__.py`, `backend/app/models/device_log_forwarding.py`, `backend/app/services/log_forwarding.py`

- [ ] **Step 1: Write the failing test** — `backend/tests/test_cert_lifecycle_model.py`

```python
from app.core.rls import TENANT_TABLES
from app.models.revoked_syslog_cert import RevokedSyslogCert


def test_ledger_table_registered_for_rls():
    assert "revoked_syslog_certs" in TENANT_TABLES


def test_ledger_model_columns():
    cols = RevokedSyslogCert.__table__.columns.keys()
    assert {"id", "tenant_id", "device_id", "serial", "reason", "revoked_at"} <= set(cols)
    assert RevokedSyslogCert.__tablename__ == "revoked_syslog_certs"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_cert_lifecycle_model.py -v` → FAIL (ModuleNotFoundError).

- [ ] **Step 3: Create the model** — `backend/app/models/revoked_syslog_cert.py`

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RevokedSyslogCert(Base):
    """Ledger of revoked per-device log-forwarding client certs (tenant-scoped, RLS).

    The CRL input for Phase 3.2-bis: each row records a revoked cert's serial so a future
    CRL can reject it at the syslog-ng receiver."""

    __tablename__ = "revoked_syslog_certs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE")
    )
    serial: Mapped[str] = mapped_column(String)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 4: Register the model + RLS table**

In `backend/app/models/__init__.py`, add (in import order, next to the other model imports):
```python
from app.models.revoked_syslog_cert import RevokedSyslogCert  # noqa: F401
```
In `backend/app/core/rls.py`, append `"revoked_syslog_certs"` to the `TENANT_TABLES` list.

- [ ] **Step 5: Add the state column** — `backend/app/models/device_log_forwarding.py`

After the `cert_not_after` column add:
```python
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 6: Create migration** — `backend/migrations/versions/0026_cert_lifecycle.py`

```python
"""revoked_syslog_certs ledger (tenant-scoped, RLS) + device_log_forwarding.revoked_at"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.db_roles import grant_app_role_statements
from app.core.rls import POLICY_NAME, policy_create_statement

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "device_log_forwarding",
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "revoked_syslog_certs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("serial", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute("ALTER TABLE revoked_syslog_certs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE revoked_syslog_certs FORCE ROW LEVEL SECURITY")
    op.execute(policy_create_statement("revoked_syslog_certs"))
    for stmt in grant_app_role_statements():
        op.execute(stmt)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON revoked_syslog_certs")
    op.execute("ALTER TABLE revoked_syslog_certs NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE revoked_syslog_certs DISABLE ROW LEVEL SECURITY")
    op.drop_table("revoked_syslog_certs")
    op.drop_column("device_log_forwarding", "revoked_at")
```

- [ ] **Step 7: Clear `revoked_at` on (re-)provision** — `backend/app/services/log_forwarding.py`

In `provision_device`, where it sets `row.provisioned_at = datetime.now(UTC)`, add directly after:
```python
    row.revoked_at = None
```

- [ ] **Step 8: Run to verify pass + lint**

Run: `cd backend && .venv/bin/pytest tests/test_cert_lifecycle_model.py -v` → 2 passed.
Run: `cd backend && .venv/bin/ruff check app/models/revoked_syslog_cert.py app/core/rls.py app/models/device_log_forwarding.py app/services/log_forwarding.py migrations/versions/0026_cert_lifecycle.py` → clean.

- [ ] **Step 9: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/models/revoked_syslog_cert.py backend/app/models/__init__.py backend/app/core/rls.py backend/app/models/device_log_forwarding.py backend/migrations/versions/0026_cert_lifecycle.py backend/app/services/log_forwarding.py backend/tests/test_cert_lifecycle_model.py
git commit -m "feat(cert-lifecycle): revoked-cert ledger (RLS) + revoked_at state column"
```

---

## Task 2: Service — `rotate_device_cert` + `revoke_device`

**Files:**
- Modify: `backend/app/services/log_forwarding.py`
- Test: `backend/tests/test_cert_lifecycle_service.py`

- [ ] **Step 1: Write the failing test** — `backend/tests/test_cert_lifecycle_service.py`

```python
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.models.device_log_forwarding import DeviceLogForwarding
from app.models.revoked_syslog_cert import RevokedSyslogCert
from app.services.log_forwarding import revoke_device, rotate_device_cert


class StubClient:
    """Records box calls; returns predictable new UUIDs."""
    def __init__(self):
        self.calls = []

    async def import_cert(self, cert_pem, key_pem, *, descr):
        self.calls.append(("import_cert", descr)); return "newcert-uuid"

    async def add_syslog_destination(self, *, hostname, port, certificate_uuid):
        self.calls.append(("add_dest", certificate_uuid)); return "newdest-uuid"

    async def delete_syslog_destination(self, dest_uuid):
        self.calls.append(("del_dest", dest_uuid)); return {}

    async def delete_cert(self, cert_uuid):
        self.calls.append(("del_cert", cert_uuid)); return {}


async def _seed_enabled(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await set_tenant_context(s, tid)
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        await s.execute(text(
            "INSERT INTO device_log_forwarding "
            "(device_id,tenant_id,enabled,cert_serial,cert_fingerprint,opnsense_cert_uuid,opnsense_dest_uuid) "
            "VALUES (:d,:t,true,'oldserial','oldfp','oldcert','olddest')"), {"d": did, "t": tid})
        await s.commit()
    return tid, did


async def test_rotate_swaps_cert_and_updates_row(db_engine):
    tid, did = await _seed_enabled(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    client = StubClient()
    async with factory() as s:
        await set_tenant_context(s, tid)
        row = await rotate_device_cert(s, tenant_id=tid, device_id=did, client=client,
                                       receiver_host="logs.example", receiver_port=6514)
        await s.commit()
    # add happened before the deletes; old dest+cert removed
    names = [c[0] for c in client.calls]
    assert names.index("add_dest") < names.index("del_dest")
    assert ("del_dest", "olddest") in client.calls and ("del_cert", "oldcert") in client.calls
    assert row.opnsense_cert_uuid == "newcert-uuid" and row.opnsense_dest_uuid == "newdest-uuid"
    assert row.cert_serial != "oldserial" and row.enabled is True


async def test_revoke_records_serial_and_disables(db_engine):
    tid, did = await _seed_enabled(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    client = StubClient()
    async with factory() as s:
        await set_tenant_context(s, tid)
        row = await revoke_device(s, tenant_id=tid, device_id=did, client=client, reason="key leak")
        await s.commit()
    assert row.enabled is False and row.revoked_at is not None
    async with factory() as s:
        await set_tenant_context(s, tid)
        led = (await s.execute(select(RevokedSyslogCert))).scalars().all()
    assert len(led) == 1 and led[0].serial == "oldserial" and led[0].reason == "key leak"


async def test_rotate_rejects_disabled_device(db_engine):
    tid, did = await _seed_enabled(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await set_tenant_context(s, tid)
        await s.execute(text("UPDATE device_log_forwarding SET enabled=false WHERE device_id=:d"), {"d": did})
        await s.commit()
    async with factory() as s:
        await set_tenant_context(s, tid)
        with pytest.raises(ValueError):
            await rotate_device_cert(s, tenant_id=tid, device_id=did, client=StubClient(),
                                     receiver_host="h", receiver_port=6514)
```

- [ ] **Step 2: Run to verify it fails** (ImportError on `rotate_device_cert`).

- [ ] **Step 3: Implement both functions** — append to `backend/app/services/log_forwarding.py`

Add the import near the top imports:
```python
from app.models.revoked_syslog_cert import RevokedSyslogCert
```
Append:
```python
async def rotate_device_cert(session: AsyncSession, *, tenant_id: uuid.UUID, device_id: uuid.UUID,
                             client, receiver_host: str, receiver_port: int) -> DeviceLogForwarding:
    """Issue a fresh device cert and swap it on the box: add the new destination BEFORE deleting the
    old one (no log gap). Requires the device to be currently forwarding."""
    row = await session.get(DeviceLogForwarding, device_id)
    if row is None or not row.enabled:
        raise ValueError("device is not currently forwarding")
    svc = SyslogCaService(session)
    ca = await svc.ensure_ca()
    cert_pem, key_pem = svc.device_cert(ca, tenant_id=tenant_id, device_id=device_id)
    serial, fp = cert_serial_and_fingerprint(cert_pem)
    not_after = cert_not_after(cert_pem)
    old_cert_uuid, old_dest_uuid = row.opnsense_cert_uuid, row.opnsense_dest_uuid
    new_cert_uuid = await client.import_cert(cert_pem.decode(), key_pem.decode(),
                                             descr=f"opngms-logs {device_id}")
    new_dest_uuid = await client.add_syslog_destination(
        hostname=receiver_host, port=receiver_port, certificate_uuid=new_cert_uuid)
    if old_dest_uuid:
        await client.delete_syslog_destination(old_dest_uuid)
    if old_cert_uuid:
        await client.delete_cert(old_cert_uuid)
    row.cert_serial, row.cert_fingerprint, row.cert_not_after = serial, fp, not_after
    row.opnsense_cert_uuid, row.opnsense_dest_uuid = new_cert_uuid, new_dest_uuid
    row.provisioned_at = datetime.now(UTC)
    await session.flush()
    return row


async def revoke_device(session: AsyncSession, *, tenant_id: uuid.UUID, device_id: uuid.UUID,
                        client, reason: str | None) -> DeviceLogForwarding:
    """Soft-revoke: snapshot the serial into the ledger, deprovision the box, mark the row revoked.
    One box-gated unit of work (the caller commits only on success)."""
    row = await session.get(DeviceLogForwarding, device_id)
    if row is None or not row.enabled:
        raise ValueError("device is not currently forwarding")
    session.add(RevokedSyslogCert(tenant_id=tenant_id, device_id=device_id,
                                  serial=row.cert_serial, reason=reason))
    if row.opnsense_dest_uuid:
        await client.delete_syslog_destination(row.opnsense_dest_uuid)
    if row.opnsense_cert_uuid:
        await client.delete_cert(row.opnsense_cert_uuid)
    row.enabled = False
    row.opnsense_dest_uuid = None
    row.opnsense_cert_uuid = None
    row.revoked_at = datetime.now(UTC)
    await session.flush()
    return row
```

- [ ] **Step 4: Run to verify pass + lint**

Run: `cd backend && TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" .venv/bin/pytest tests/test_cert_lifecycle_service.py -v` → 3 passed.
Run: `cd backend && .venv/bin/ruff check app/services/log_forwarding.py` → clean.

- [ ] **Step 5: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/services/log_forwarding.py backend/tests/test_cert_lifecycle_service.py
git commit -m "feat(cert-lifecycle): rotate_device_cert + revoke_device service ops"
```

---

## Task 3: API — `/rotate` + `/revoke` endpoints

**Files:**
- Modify: `backend/app/schemas/log_forwarding.py`, `backend/app/api/log_forwarding.py`
- Test: `backend/tests/test_cert_lifecycle_api.py`

- [ ] **Step 1: Write the failing test** — `backend/tests/test_cert_lifecycle_api.py`

```python
import types
import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from tests.factories import make_membership, make_user


async def _seed(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        op = await make_user(s, email="op@x.io", password="pw12345")
        ro = await make_user(s, email="ro@x.io", password="pw12345")
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await make_membership(s, user_id=op.id, tenant_id=tid, role="operator")
        await make_membership(s, user_id=ro.id, tenant_id=tid, role="read_only")
        await set_tenant_context(s, tid)
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        await s.commit()
    return tid, did


def _row(did, **kw):
    base = dict(device_id=did, enabled=True, cert_serial="newserial", cert_fingerprint="fp",
                provisioned_at=None, cert_not_after=None, revoked_at=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


async def _login(api_client, email):
    r = await api_client.post("/api/login", json={"email": email, "password": "pw12345"})
    assert r.status_code == 200


async def test_rotate_operator_ok(api_client, db_engine, monkeypatch):
    tid, did = await _seed(db_engine)

    async def fake(session, *, tenant_id, device_id, client, receiver_host, receiver_port):
        return _row(did, cert_serial="rotated")
    monkeypatch.setattr("app.api.log_forwarding.rotate_device_cert", fake)
    await _login(api_client, "op@x.io")
    r = await api_client.post(f"/api/tenants/{tid}/devices/{did}/log-forwarding/rotate")
    assert r.status_code == 200, r.text
    assert r.json()["cert_serial"] == "rotated"


async def test_rotate_read_only_denied(api_client, db_engine):
    tid, did = await _seed(db_engine)
    await _login(api_client, "ro@x.io")
    r = await api_client.post(f"/api/tenants/{tid}/devices/{did}/log-forwarding/rotate")
    assert r.status_code == 403


async def test_rotate_409_when_not_forwarding(api_client, db_engine, monkeypatch):
    tid, did = await _seed(db_engine)

    async def fake(session, *, tenant_id, device_id, client, receiver_host, receiver_port):
        raise ValueError("device is not currently forwarding")
    monkeypatch.setattr("app.api.log_forwarding.rotate_device_cert", fake)
    await _login(api_client, "op@x.io")
    r = await api_client.post(f"/api/tenants/{tid}/devices/{did}/log-forwarding/rotate")
    assert r.status_code == 409


async def test_revoke_operator_ok(api_client, db_engine, monkeypatch):
    tid, did = await _seed(db_engine)

    async def fake(session, *, tenant_id, device_id, client, reason):
        assert reason == "key leak"
        return _row(did, enabled=False, revoked_at=datetime(2026, 6, 1, tzinfo=UTC))
    monkeypatch.setattr("app.api.log_forwarding.revoke_device", fake)
    await _login(api_client, "op@x.io")
    r = await api_client.post(f"/api/tenants/{tid}/devices/{did}/log-forwarding/revoke",
                              json={"reason": "key leak"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is False and body["revoked_at"].startswith("2026-06-01")
```

- [ ] **Step 2: Run to verify it fails** (404/405 — endpoints don't exist; or KeyError on `revoked_at`).

- [ ] **Step 3: Schema** — `backend/app/schemas/log_forwarding.py`

Add `revoked_at` to `LogForwardingOut`:
```python
    revoked_at: datetime | None = None
```
Add a request model:
```python
from pydantic import BaseModel, Field


class RevokeIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
```
(Keep the existing `LogForwardingOut`; add `Field` to the import if not present.)

- [ ] **Step 4: Endpoints + `_out`** — `backend/app/api/log_forwarding.py`

Update the service import to include the new ops:
```python
from app.services.log_forwarding import deprovision_device, provision_device, revoke_device, rotate_device_cert
```
Update the schema import:
```python
from app.schemas.log_forwarding import LogForwardingOut, RevokeIn
```
In `_out`, add `revoked_at=row.revoked_at` to the populated branch (the `row is None` branch already returns the disabled default):
```python
    return LogForwardingOut(device_id=row.device_id, enabled=row.enabled, cert_serial=row.cert_serial,
                            cert_fingerprint=row.cert_fingerprint, provisioned_at=row.provisioned_at,
                            cert_not_after=row.cert_not_after, revoked_at=row.revoked_at)
```
Append the two endpoints (mirror the existing `enable` handler — `CONFIG_PUSH`, CSRF, audit, commit):
```python
@router.post("/rotate", response_model=LogForwardingOut, dependencies=[Depends(enforce_csrf)])
async def rotate_log_forwarding(
    tenant_id: uuid.UUID, device_id: uuid.UUID, request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.CONFIG_PUSH)),
    session: AsyncSession = Depends(get_session),
) -> LogForwardingOut:
    device = await _device(session, tenant_id, device_id)
    s = get_settings()
    try:
        row = await rotate_device_cert(session, tenant_id=tenant_id, device_id=device_id,
                                       client=_client(device), receiver_host=s.syslog_receiver_host,
                                       receiver_port=s.syslog_tls_port)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OpnsenseError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=type(exc).__name__) from exc
    await AuditService(session).record(
        actor_user_id=ctx.user.id, tenant_id=tenant_id, action="log_forwarding.rotate",
        target_type="device", target_id=str(device_id),
        ip=request.client.host if request.client else None, details={"serial": row.cert_serial})
    out = _out(row, device_id=device_id)
    await session.commit()
    return out


@router.post("/revoke", response_model=LogForwardingOut, dependencies=[Depends(enforce_csrf)])
async def revoke_log_forwarding(
    tenant_id: uuid.UUID, device_id: uuid.UUID, request: Request, body: RevokeIn,
    ctx: TenantContext = Depends(require_tenant(Action.CONFIG_PUSH)),
    session: AsyncSession = Depends(get_session),
) -> LogForwardingOut:
    device = await _device(session, tenant_id, device_id)
    try:
        row = await revoke_device(session, tenant_id=tenant_id, device_id=device_id,
                                  client=_client(device), reason=body.reason)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OpnsenseError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=type(exc).__name__) from exc
    await AuditService(session).record(
        actor_user_id=ctx.user.id, tenant_id=tenant_id, action="log_forwarding.revoke",
        target_type="device", target_id=str(device_id),
        ip=request.client.host if request.client else None,
        details={"serial": row.cert_serial, "reason": body.reason})
    out = _out(row, device_id=device_id)
    await session.commit()
    return out
```

- [ ] **Step 5: Run to verify pass + lint**

Run: `cd backend && TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" .venv/bin/pytest tests/test_cert_lifecycle_api.py -v` → 4 passed.
Run: `cd backend && TEST_DATABASE_URL=… .venv/bin/pytest tests/ -k log_forwarding -q` → no regressions.
Run: `cd backend && .venv/bin/ruff check app/api/log_forwarding.py app/schemas/log_forwarding.py` → clean.

- [ ] **Step 6: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/schemas/log_forwarding.py backend/app/api/log_forwarding.py backend/tests/test_cert_lifecycle_api.py
git commit -m "feat(cert-lifecycle): rotate + revoke endpoints (CONFIG_PUSH, audited)"
```

---

# PHASE B — frontend

## Task 4: Card — Rotate + Revoke + Revoked badge

**Files:**
- Modify: `frontend/src/api/schema.d.ts` + `frontend/openapi.json` (regen), `frontend/src/logs/logForwardingHooks.ts`, `frontend/src/components/LogForwardingCard.tsx`
- Create: `frontend/src/components/__tests__/certLifecycle.test.tsx`

- [ ] **Step 1: Regenerate the client**

Run: `cd frontend && npm run gen:api` then `grep -c "revoked_at" src/api/schema.d.ts` → > 0.

- [ ] **Step 2: Write the failing test** — `frontend/src/components/__tests__/certLifecycle.test.tsx`

(Mirror the `withTenant`/base-URL/imports from `src/components/__tests__/logForwarding.test.tsx`; use the real `ConfirmModal` confirm testid `confirm-ok`.)

```tsx
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { LogForwardingCard } from "../LogForwardingCard";
import { TenantContext } from "../../tenant/TenantProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

function withTenant(node: ReactNode, role: string = "operator") {
  return (
    <TenantContext.Provider value={{
      tenants: [{ id: "t1", name: "Acme", slug: "acme", role }],
      activeId: "t1", setActiveId: () => {}, loading: false,
    }}>{node}</TenantContext.Provider>
  );
}

const BASE = "http://localhost:3000/api/tenants/t1/devices/d1/log-forwarding";
const enabledBody = {
  device_id: "d1", enabled: true, cert_serial: "ab", cert_fingerprint: "deadbeefcafe",
  provisioned_at: "2026-06-01T00:00:00Z", cert_not_after: "2027-01-01T00:00:00Z",
  last_log_at: "2026-06-01T10:00:00Z", revoked_at: null,
};

describe("LogForwardingCard cert lifecycle", () => {
  it("rotates the certificate", async () => {
    let rotated = false;
    server.use(
      http.get(BASE, () => HttpResponse.json(enabledBody)),
      http.post(`${BASE}/rotate`, () => { rotated = true; return HttpResponse.json(enabledBody); }),
    );
    renderWithProviders(withTenant(<LogForwardingCard deviceId="d1" />, "operator"));
    await userEvent.click(await screen.findByTestId("lf-rotate"));
    await userEvent.click(await screen.findByTestId("confirm-ok"));
    await waitFor(() => expect(rotated).toBe(true));
  });

  it("revokes and shows the Revoked badge", async () => {
    let enabled = true;
    server.use(
      http.get(BASE, () => HttpResponse.json({ ...enabledBody, enabled,
        revoked_at: enabled ? null : "2026-06-02T00:00:00Z" })),
      http.post(`${BASE}/revoke`, () => { enabled = false; return HttpResponse.json({
        ...enabledBody, enabled: false, revoked_at: "2026-06-02T00:00:00Z" }); }),
    );
    renderWithProviders(withTenant(<LogForwardingCard deviceId="d1" />, "operator"));
    await userEvent.click(await screen.findByTestId("lf-revoke"));
    await userEvent.click(await screen.findByTestId("confirm-ok"));
    await waitFor(() => expect(screen.getByTestId("lf-status")).toHaveTextContent(/revoked/i));
  });

  it("hides rotate/revoke for read_only", async () => {
    server.use(http.get(BASE, () => HttpResponse.json(enabledBody)));
    renderWithProviders(withTenant(<LogForwardingCard deviceId="d1" />, "read_only"));
    expect(await screen.findByTestId("lf-status")).toBeInTheDocument();
    expect(screen.queryByTestId("lf-rotate")).toBeNull();
    expect(screen.queryByTestId("lf-revoke")).toBeNull();
  });
});
```

- [ ] **Step 3: Run to verify it fails** (`cd frontend && npm test -- certLifecycle`).

- [ ] **Step 4: Hooks** — add to `frontend/src/logs/logForwardingHooks.ts`

```ts
export function useRotateLogForwarding(deviceId: string) {
  const { activeId } = useTenant();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<LogForwardingOut> => {
      const { data, error } = await api.POST(
        "/api/tenants/{tenant_id}/devices/{device_id}/log-forwarding/rotate",
        { params: { path: { tenant_id: activeId!, device_id: deviceId } } });
      if (error || !data) throw new Error("rotate failed");
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["log-forwarding", activeId, deviceId] }),
  });
}

export function useRevokeLogForwarding(deviceId: string) {
  const { activeId } = useTenant();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (reason: string | null): Promise<LogForwardingOut> => {
      const { data, error } = await api.POST(
        "/api/tenants/{tenant_id}/devices/{device_id}/log-forwarding/revoke",
        { params: { path: { tenant_id: activeId!, device_id: deviceId } }, body: { reason } });
      if (error || !data) throw new Error("revoke failed");
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["log-forwarding", activeId, deviceId] }),
  });
}
```

- [ ] **Step 5: Card** — update `frontend/src/components/LogForwardingCard.tsx`

Add imports + hooks usage:
```tsx
import { useRotateLogForwarding, useRevokeLogForwarding } from "../logs/logForwardingHooks";
```
Inside the component (next to `enable`/`disable`):
```tsx
  const rotate = useRotateLogForwarding(deviceId);
  const revoke = useRevokeLogForwarding(deviceId);
  const [lifecycle, setLifecycle] = useState<null | "rotate" | "revoke">(null);
```
Compute a revoked state and use it in the status badge:
```tsx
  const revoked = !enabled && !!s?.revoked_at;
```
Change the status badge to three states:
```tsx
          <Badge color={enabled ? "green" : revoked ? "red" : "gray"} data-testid="lf-status">
            {enabled ? "Enabled" : revoked ? "Revoked" : "Disabled"}
          </Badge>
```
Extend the `run` handler to cover lifecycle actions:
```tsx
  async function runLifecycle(action: "rotate" | "revoke") {
    setLifecycle(null);
    try {
      if (action === "rotate") await rotate.mutateAsync();
      else await revoke.mutateAsync(null);
    } catch {
      // isError drives the alert
    }
  }
```
Add the two buttons inside the `canWrite` group, shown when `enabled`:
```tsx
            {enabled && (
              <Button data-testid="lf-rotate" variant="light" loading={rotate.isPending}
                      onClick={() => setLifecycle("rotate")}>
                Rotate cert
              </Button>
            )}
            {enabled && (
              <Button data-testid="lf-revoke" color="red" loading={revoke.isPending}
                      onClick={() => setLifecycle("revoke")}>
                Revoke
              </Button>
            )}
```
Add a second `ConfirmModal` for lifecycle (after the existing enable/disable one):
```tsx
      <ConfirmModal
        opened={lifecycle !== null}
        onClose={() => setLifecycle(null)}
        onConfirm={() => runLifecycle(lifecycle!)}
        title={lifecycle === "rotate" ? "Rotate certificate?" : "Revoke certificate?"}
        body={lifecycle === "rotate"
          ? "Issues a new client certificate and swaps it on the device — no logs are lost."
          : "Removes the certificate and marks it revoked. Re-enabling will issue a brand-new certificate."}
      />
```
Add `(rotate.isError || revoke.isError)` to the existing error-alert condition. If the existing Disable button sits next to these, keep it; the three buttons (Rotate / Revoke / Disable) can share the `Group`.

- [ ] **Step 6: Verify + build gate**

Run: `cd frontend && npm test -- certLifecycle && npm test -- logForwarding && npm run build`
All MUST pass (the existing logForwarding test must still pass — the status badge change keeps "Enabled"/"Disabled" text for those cases).

- [ ] **Step 7: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add frontend/src/api/schema.d.ts frontend/openapi.json frontend/src/logs/logForwardingHooks.ts frontend/src/components/LogForwardingCard.tsx frontend/src/components/__tests__/certLifecycle.test.tsx
git commit -m "feat(cert-lifecycle): rotate + revoke actions + Revoked badge on the card"
```

---

## Final verification

- [ ] **Backend:** `cd backend && TEST_DATABASE_URL=… .venv/bin/pytest -q` → all pass; `ruff check app` clean.
- [ ] **Frontend:** `cd frontend && npm run build && npx vitest run` → all pass.
- [ ] **Security review:** dispatch `security-reviewer` (rotate/revoke are CONFIG_PUSH+CSRF+audited; the ledger is RLS tenant-scoped; box-gated transaction → no divergence; only serial/fingerprint/expiry surfaced, never the key; soft revocation honestly labeled — no CRL enforcement yet). Address BLOCKER/IMPORTANT.
- [ ] **Finish:** `superpowers:finishing-a-development-branch` → PR with green CI, merge.

---

## Self-review notes (author)

- **Spec coverage:** ledger table + `revoked_at` column + TENANT_TABLES + provision clears revoked_at (Task 1) ✓; `rotate_device_cert` add-first/delete-after + `revoke_device` ledger snapshot + box-gated transaction + 409-on-disabled (Task 2) ✓; `/rotate` + `/revoke` endpoints CONFIG_PUSH+CSRF+audit + `OpnsenseError→502` + `ValueError→409` + `_out` revoked_at + `RevokeIn` (Task 3) ✓; frontend rotate/revoke buttons + Revoked badge + read_only gating (Task 4) ✓; RLS tenant-scoping of the ledger (Task 1 migration) ✓.
- **Type consistency:** `rotate_device_cert(session, *, tenant_id, device_id, client, receiver_host, receiver_port)` and `revoke_device(session, *, tenant_id, device_id, client, reason)` are identical across Task 2 (def), Task 3 (call + monkeypatch fakes); `RevokedSyslogCert` fields (Task 1) used by `revoke_device` (Task 2) and asserted in tests; `LogForwardingOut.revoked_at` (Task 3 schema) consumed by the TS type + card (Task 4); `RevokeIn.reason` ↔ `{reason}` body (Task 3 ↔ Task 4 hook).
- **Risk flags:** (a) the API tests monkeypatch the service (the real box mechanics live in Task 2's stub-client tests) — intentional, keeps API tests box-free; (b) openapi-fetch path literals + body typing for `/revoke` — Step 4 uses explicit literals; (c) the existing `logForwarding.test.tsx` must stay green after the badge change — Step 6 re-runs it.
