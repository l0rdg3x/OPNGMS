# OPNGMS — Phase 4 / Milestone 4D-a: Config Change & Push Pipeline (dry-run) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A safe, tenant-scoped pipeline to propose a granular firewall **alias** change, preview it (secret-safe), and schedule it immediately or for a future date/time; an apply job re-checks the config hash (staleness guard → `conflict`, no clobber), serializes per device, runs a **dry-run** apply (no real mutation), audits every step, and refreshes the snapshot. Gated by a new `CONFIG_PUSH` RBAC action.

**Architecture:** Reuses the stack. New `config_changes` table (RLS). A change service computes the baseline hash (4A) and the preview. An ARQ job applies the change (event-driven, immediate or ARQ-deferred), guarded by a Postgres advisory lock + a `canonical_hash` re-check. The real firewall mutation is behind a `dry_run`-default connector method (real endpoints TO VERIFY → 4D-b). No firewall is changed in 4D-a.

**Tech Stack:** Python 3.12+, FastAPI/SQLAlchemy async, Postgres + RLS, ARQ + Redis (deferred jobs), Fernet, pytest + respx.

---

## Context for the implementer (read first)

Codebase is **English** — write all code/comments/messages in English. Phases 1–4C in `main`.

- **Model + RLS + migration reference**: `app/models/config_snapshot.py` / `migrations/versions/0009_config_snapshots.py` (normal table, UUIDPKMixin, FK CASCADE, RLS enable/force/policy + grant). `app/core/rls.py` (`TENANT_TABLES`).
- **RBAC**: `app/core/rbac.py` — add `CONFIG_PUSH = "config.push"` to `Action`, and `Action.CONFIG_PUSH: {TENANT_ADMIN, OPERATOR}` to `_TENANT_MATRIX`. Update `tests/test_rbac_matrix.py` accordingly.
- **Connector**: `app/connectors/opnsense/client.py` — `_request(path)` (SSRF-guarded GET) + `_get`/`get_config_backup`. 4D-a generalizes `_request` to support POST (`method`/`json` params) and adds `_post` + `apply_alias`.
- **Staleness**: `app/services/config_diff.py` `canonical_hash(xml)` (4A) + `OpnsenseClient.get_config_backup()` (4A) to re-read the config at apply time.
- **Snapshot repo**: `app/repositories/config_snapshot.py` (`latest(device_id)`) for the baseline hash.
- **Audit**: `app/services/audit.py` — `AuditService(session).record(actor_user_id, tenant_id, action, target_type, target_id, ip, details)` (see `app/api/devices.py` for usage).
- **Worker**: `app/worker.py` — pattern for jobs/`WorkerSettings`; the apply job mirrors `backup_device_config` (owner session, decrypt secrets, build `OpnsenseClient`).
- **API/RLS reference**: `app/api/config.py` (4A/4B tenant-scoped router on `/api/tenants/{tenant_id}`), `app/api/devices.py` (CSRF `enforce_csrf` on mutations, audit), `tests/test_config_rls_api.py` (real `opngms_app` RLS proof), `tests/test_events_api.py` (login/RBAC helpers).
- **Tests**: `tests/conftest.py` (`db_engine`, `two_tenants`, `api_client`, `app_role_api_client`). `config_changes` is a normal table → created by `create_all`, covered by `enable_rls` once in `TENANT_TABLES`.

**Test command** (from `backend/`):
```
TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" \
ADMIN_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" \
.venv/bin/python -m pytest -q
```
Current suite: **206 tests green** (backend). `alembic check` procedure per 4A/3A.

**Safety guardrails:** the apply NEVER mutates a real firewall in 4D-a — `apply_alias(dry_run=True)` is the only call path; the job passes `dry_run=True`. The staleness guard MUST run before any apply. No secret value is returned by any endpoint. ⚠️ OPNsense alias endpoints are TO VERIFY (mocked).

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `app/models/config_change.py` | `ConfigChange` model | Create |
| `app/models/__init__.py`, `app/core/rls.py`, `app/core/rbac.py` | export / RLS / `CONFIG_PUSH` | Modify |
| `migrations/versions/0010_config_changes.py` | table + RLS + grant | Create |
| `app/connectors/opnsense/client.py` | `_request` POST support + `_post` + `apply_alias` | Modify |
| `app/services/config_push.py` | create / preview / apply (staleness, lock, dry-run) | Create |
| `app/core/queue.py` | ARQ enqueuer (injectable) | Create |
| `app/worker.py` | `apply_config_change` job | Modify |
| `app/schemas/config.py` | `ConfigChangeIn`, `ConfigChangeOut`, `ScheduleIn`, `ChangePreview` | Modify |
| `app/repositories/config_change.py` | `ConfigChangeRepository` | Create |
| `app/api/config.py` | create / preview / schedule / list / cancel | Modify |
| `app/main.py` | (router already registered) | — |
| tests | model+RLS, connector, service, worker, API+isolation, rbac | Create/Modify |

---

## Task 1: `config_changes` model + migration 0010 + RLS + `CONFIG_PUSH`

**Files:**
- Create: `app/models/config_change.py`, `migrations/versions/0010_config_changes.py`
- Modify: `app/models/__init__.py`, `app/core/rls.py`, `app/core/rbac.py`
- Create: `tests/test_config_change_model.py`; Modify: `tests/test_rls_isolation.py`, `tests/test_rbac_matrix.py`

- [ ] **Step 1: Write the model**

Create `app/models/config_change.py` (mirror `config_snapshot.py`):
```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class ConfigChange(UUIDPKMixin, Base):
    __tablename__ = "config_changes"
    __table_args__ = (
        Index("ix_config_changes_tenant_device_created", "tenant_id", "device_id", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String)                          # e.g. 'alias'
    operation: Mapped[str] = mapped_column(String)                     # 'add' | 'set' | 'delete'
    target: Mapped[str] = mapped_column(String, default="", server_default="")
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    baseline_hash: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="draft", server_default="draft")
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    result: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 2: Export, RLS, RBAC**

- `app/models/__init__.py`: export `ConfigChange` (+ `__all__`).
- `app/core/rls.py`: `TENANT_TABLES: list[str] = ["devices", "metrics", "alerts", "events", "config_snapshots", "config_changes"]`.
- `app/core/rbac.py`: add `CONFIG_PUSH = "config.push"` to `Action`, and `Action.CONFIG_PUSH: {TENANT_ADMIN, OPERATOR}` to `_TENANT_MATRIX`.

- [ ] **Step 3: Migration 0010** (mirror 0009; `down_revision = "0009"`)

Create `migrations/versions/0010_config_changes.py` — `create_table` (columns above) + 3 indexes + FK CASCADE + RLS enable/force/policy on `config_changes` + `grant_app_role_statements()`; symmetric downgrade (revoke + drop policy + disable RLS + drop table). Use the 0009 file as the exact template.

- [ ] **Step 4: Tests**

Create `tests/test_config_change_model.py` (owner insert, using a seeded device for the FK — see `tests/test_config_snapshot_model.py`). In `tests/test_rls_isolation.py` add `test_config_changes_isolated_cross_tenant` (real `opngms_app`, raw SQL, context A → only A's rows) + a static `"config_changes" in TENANT_TABLES` check (mirror the events/config_snapshots ones). In `tests/test_rbac_matrix.py` add assertions: `CONFIG_PUSH` allowed for tenant_admin + operator + superadmin, denied for read_only.

- [ ] **Step 5: Run + alembic check**

`... pytest tests/test_config_change_model.py tests/test_rls_isolation.py tests/test_rbac_matrix.py -v` → PASS. Whole suite green. `alembic check` clean on a fresh DB; verify the 0010 downgrade/upgrade round-trip.

- [ ] **Step 6: Commit**
```bash
git add app/models/config_change.py app/models/__init__.py app/core/rls.py app/core/rbac.py \
        migrations/versions/0010_config_changes.py tests/test_config_change_model.py \
        tests/test_rls_isolation.py tests/test_rbac_matrix.py
git commit -m "feat(backend): config_changes table + RLS + CONFIG_PUSH action (migration 0010)"
```

---

## Task 2: Connector `apply_alias` (dry-run) + guarded POST

**Files:**
- Modify: `app/connectors/opnsense/client.py`
- Create: `tests/test_connector_apply_alias.py`

- [ ] **Step 1: Write the failing respx test**

Create `tests/test_connector_apply_alias.py`:
```python
import httpx
import respx

from app.connectors.opnsense.client import OpnsenseClient


async def test_apply_alias_dry_run_does_no_http():
    # dry_run=True must perform NO HTTP and return a stub.
    client = OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)
    out = await client.apply_alias("set", {"name": "myalias", "content": ["1.2.3.4"]}, dry_run=True)
    assert out["dry_run"] is True
    assert out["operation"] == "set"


@respx.mock
async def test_apply_alias_real_posts_and_reconfigures():
    set_route = respx.post(url__regex=r".*/api/firewall/alias/setItem.*").mock(
        return_value=httpx.Response(200, json={"result": "saved"})
    )
    rec_route = respx.post(url__regex=r".*/api/firewall/alias/reconfigure.*").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )
    client = OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)
    out = await client.apply_alias("set", {"name": "myalias"}, dry_run=False)
    assert out["dry_run"] is False
    assert set_route.called and rec_route.called
```

- [ ] **Step 2: Run and verify it fails** — `... pytest tests/test_connector_apply_alias.py -v` → FAIL.

- [ ] **Step 3: Generalize `_request` for POST, add `_post` + `apply_alias`**

In `app/connectors/opnsense/client.py`, give `_request` optional `method`/`json` (default GET/None) and use `client.request(...)`; keep `_get`/`get_config_backup` calling it as before (regression-safe). Add:
```python
    async def _request(self, path: str, method: str = "GET", json: dict | None = None) -> "httpx.Response":
        # ... existing SSRF guard + IP pinning unchanged ...
        try:
            async with httpx.AsyncClient(
                verify=self._verify, timeout=self._timeout, auth=self._auth, follow_redirects=False
            ) as client:
                resp = await client.request(
                    method, url, headers={"Host": host}, extensions={"sni_hostname": host}, json=json
                )
        except httpx.HTTPError as exc:
            raise ReachabilityError("device unreachable") from exc
        # ... existing 401/403 -> AuthError, >=400 -> ApiError ...
        return resp

    async def _post(self, path: str, json: dict) -> dict:
        resp = await self._request(path, "POST", json)
        try:
            return resp.json()
        except ValueError as exc:
            raise ParseError("response not interpretable") from exc

    async def apply_alias(self, operation: str, payload: dict, *, dry_run: bool = True) -> dict:
        """Apply a firewall alias change. dry_run=True (default) performs NO mutation.

        NOTE: endpoints `firewall/alias/{addItem,setItem,delItem}` + `firewall/alias/reconfigure`
        and the payload shape TO VERIFY against a real OPNsense device (4D-b). Goes through the
        single SSRF-guarded HTTP boundary.
        """
        if dry_run:
            return {"dry_run": True, "operation": operation, "target": payload.get("name", "")}
        endpoints = {
            "add": "firewall/alias/addItem",
            "set": "firewall/alias/setItem",
            "delete": "firewall/alias/delItem",
        }
        if operation not in endpoints:
            raise ApiError(0, f"unknown alias operation: {operation}")
        res = await self._post(endpoints[operation], {"alias": payload})
        await self._post("firewall/alias/reconfigure", {})
        return {"dry_run": False, "result": res}
```
(Keep `_get` = `self._request(path)` + `.json()`; only generalize `_request` and add the two methods. Existing connector tests must stay green — they prove the GET path is unchanged.)

- [ ] **Step 4: Run + regression** — `... pytest tests/test_connector_apply_alias.py tests/test_connector_ids.py tests/test_connector_config.py tests/test_opnsense_client.py -v` → all PASS. Whole suite green.

- [ ] **Step 5: Commit**
```bash
git add app/connectors/opnsense/client.py tests/test_connector_apply_alias.py
git commit -m "feat(backend): connector apply_alias (dry-run default) + guarded POST"
```

---

## Task 3: Change service — create + preview

**Files:**
- Create: `app/services/config_push.py` (the create + preview parts; apply added in Task 4)
- Create: `tests/test_config_push_service.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config_push_service.py`:
```python
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.config_change import ConfigChange
from app.services.config_push import create_change, preview_change


async def _device_with_snapshot(db_engine, tenant_id, canon="h1"):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id, :t, 'fw', 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tenant_id},
        )
        await s.execute(
            text(
                "INSERT INTO config_snapshots (id, tenant_id, device_id, canonical_hash, content_enc) "
                "VALUES (:id, :t, :d, :h, '\\x00'::bytea)"
            ),
            {"id": uuid.uuid4(), "t": tenant_id, "d": did, "h": canon},
        )
        await s.commit()
    return did


async def test_create_change_captures_baseline_hash(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device_with_snapshot(db_engine, tenant_a, canon="base-h")
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ch = await create_change(
            s, tenant_id=tenant_a, device_id=did, created_by=uuid.uuid4(),
            kind="alias", operation="set", target="myalias", payload={"name": "myalias", "content": ["1.2.3.4"]},
        )
        await s.commit()
        cid = ch.id
    async with factory() as s:
        row = await s.get(ConfigChange, cid)
    assert row.status == "draft"
    assert row.baseline_hash == "base-h"   # captured from the latest snapshot
    assert row.payload["name"] == "myalias"


def test_preview_is_secret_safe_summary():
    ch = ConfigChange(
        tenant_id=uuid.uuid4(), device_id=uuid.uuid4(), created_by=uuid.uuid4(),
        kind="alias", operation="set", target="myalias",
        payload={"name": "myalias", "content": ["1.2.3.4"]}, baseline_hash="h",
    )
    p = preview_change(ch)
    assert p["operation"] == "set" and p["kind"] == "alias" and p["target"] == "myalias"
    assert p["new"] == {"name": "myalias", "content": ["1.2.3.4"]}
```

- [ ] **Step 2: Run and verify it fails** — FAIL (module missing).

- [ ] **Step 3: Implement the service (create + preview)**

Create `app/services/config_push.py`:
```python
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config_change import ConfigChange
from app.repositories.config_snapshot import ConfigSnapshotRepository


async def create_change(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    created_by: uuid.UUID,
    kind: str,
    operation: str,
    target: str,
    payload: dict,
) -> ConfigChange:
    """Create a draft change, capturing the baseline canonical_hash (4A) for the staleness guard."""
    snap = await ConfigSnapshotRepository(session, tenant_id).latest(device_id)
    baseline = snap.canonical_hash if snap else ""
    change = ConfigChange(
        tenant_id=tenant_id, device_id=device_id, created_by=created_by,
        kind=kind, operation=operation, target=target, payload=payload,
        baseline_hash=baseline, status="draft",
    )
    session.add(change)
    await session.flush()
    return change


def preview_change(change: ConfigChange) -> dict:
    """Secret-safe summary of what the change would do (no firewall contact, no secret values).

    Aliases carry no secrets; for secret-bearing kinds later, redact sensitive payload keys here.
    """
    return {
        "operation": change.operation,
        "kind": change.kind,
        "target": change.target,
        "new": change.payload,
    }
```

- [ ] **Step 4: Run and verify it passes** — PASS. Whole suite green.

- [ ] **Step 5: Commit**
```bash
git add app/services/config_push.py tests/test_config_push_service.py
git commit -m "feat(backend): config push service — create draft (baseline hash) + secret-safe preview"
```

---

## Task 4: Apply job + worker (staleness guard, advisory lock, dry-run, audit)

**Files:**
- Modify: `app/services/config_push.py` (add `apply_change`), `app/worker.py`
- Create: `app/core/queue.py`
- Create: `tests/test_config_push_apply.py`; Modify: `tests/test_worker_config.py`

- [ ] **Step 1: Write the failing apply tests**

Create `tests/test_config_push_apply.py`. Use a fake client. Cover: apply on matching hash → `applied` (dry-run result); stale hash → `conflict` (no apply); non-`scheduled` status → no-op. (Build the change directly + set status=`scheduled`.)
```python
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.config_change import ConfigChange
from app.services.config_push import apply_change


class FakeClient:
    def __init__(self, xml):
        self._xml = xml
    async def get_config_backup(self):
        return self._xml
    async def apply_alias(self, operation, payload, *, dry_run=True):
        return {"dry_run": dry_run, "operation": operation}


XML = "<opnsense><system><hostname>fw1</hostname></system></opnsense>"
# canonical_hash(XML) computed by the service; the test reads it back via the same fn.


async def _scheduled_change(db_engine, tenant_id, baseline_hash) -> uuid.UUID:
    from app.services.config_diff import canonical_hash  # to align baseline with XML if needed
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    cid = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id, :t, 'fw', 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tenant_id},
        )
        await s.execute(
            text(
                "INSERT INTO config_changes (id, tenant_id, device_id, created_by, kind, operation, target, payload, baseline_hash, status) "
                "VALUES (:id, :t, :d, :u, 'alias', 'set', 'a', '{}'::jsonb, :h, 'scheduled')"
            ),
            {"id": cid, "t": tenant_id, "d": did, "u": uuid.uuid4(), "h": baseline_hash},
        )
        await s.commit()
    return cid


async def test_apply_matching_hash_applies_dry_run(db_engine, two_tenants):
    from app.services.config_diff import canonical_hash
    tenant_a, _ = two_tenants
    cid = await _scheduled_change(db_engine, tenant_a, baseline_hash=canonical_hash(XML))
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ch = await s.get(ConfigChange, cid)
        status = await apply_change(s, ch, FakeClient(XML), now=datetime.now(timezone.utc))
        await s.commit()
    assert status == "applied"
    async with factory() as s:
        ch = await s.get(ConfigChange, cid)
    assert ch.status == "applied" and ch.result.get("dry_run") is True


async def test_apply_stale_hash_conflicts(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    cid = await _scheduled_change(db_engine, tenant_a, baseline_hash="STALE")  # != hash(XML)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ch = await s.get(ConfigChange, cid)
        status = await apply_change(s, ch, FakeClient(XML), now=datetime.now(timezone.utc))
        await s.commit()
    assert status == "conflict"
    async with factory() as s:
        ch = await s.get(ConfigChange, cid)
    assert ch.status == "conflict"
```

- [ ] **Step 2: Run and verify it fails** — FAIL (`apply_change` missing).

- [ ] **Step 3: Implement `apply_change`**

In `app/services/config_push.py` add:
```python
import hashlib
from datetime import datetime

from sqlalchemy import text

from app.connectors.opnsense.client import OpnsenseError
from app.services.config_diff import canonical_hash


def _advisory_key(device_id: uuid.UUID) -> int:
    """Stable signed 64-bit key for pg_try_advisory_xact_lock, derived from device_id."""
    digest = hashlib.sha1(str(device_id).encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


async def apply_change(session: AsyncSession, change: ConfigChange, client, now: datetime) -> str:
    """Apply a scheduled change. Returns the new status. Dry-run; staleness-guarded; per-device serialized."""
    if change.status != "scheduled":
        return change.status
    # Per-device serialization: transaction-scoped advisory lock (auto-released at commit/rollback).
    got = (
        await session.execute(
            text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": _advisory_key(change.device_id)}
        )
    ).scalar_one()
    if not got:
        return change.status  # another apply holds the device lock; leave scheduled for retry
    # Staleness guard: re-read the current config and compare canonical hashes.
    try:
        xml = await client.get_config_backup()
        current = canonical_hash(xml)
    except (OpnsenseError, ValueError, SyntaxError):
        change.status = "failed"
        change.result = {"error": "could not read current config"}
        await session.flush()
        return "failed"
    if current != change.baseline_hash:
        change.status = "conflict"
        change.result = {"reason": "config changed since proposal", "baseline": change.baseline_hash}
        await session.flush()
        return "conflict"
    change.status = "applying"
    await session.flush()
    try:
        res = await client.apply_alias(change.operation, change.payload, dry_run=True)
        change.status = "applied"
        change.applied_at = now
        change.result = res
    except OpnsenseError:
        change.status = "failed"
        change.result = {"error": "apply failed"}
    await session.flush()
    return change.status
```

- [ ] **Step 4: Create the ARQ enqueuer (injectable)**

Create `app/core/queue.py`:
```python
from arq import create_pool
from arq.connections import RedisSettings

from app.core.config import get_settings


async def enqueue(name: str, *args, defer_until=None) -> None:
    """Enqueue an ARQ job (immediate, or deferred to `defer_until`). One pool per call (low volume)."""
    pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    try:
        kwargs = {"_defer_until": defer_until} if defer_until is not None else {}
        await pool.enqueue_job(name, *args, **kwargs)
    finally:
        await pool.close()


async def get_enqueuer():
    """FastAPI dependency returning the enqueue callable (overridable in tests)."""
    return enqueue
```

- [ ] **Step 5: Worker job wiring**

In `app/worker.py` add `from app.services.config_push import apply_change` and:
```python
async def apply_config_change(ctx: dict, change_id: str) -> str:
    """Job: apply a scheduled config change (dry-run), staleness-guarded + audited."""
    from datetime import datetime, timezone

    from app.models.config_change import ConfigChange
    from app.services.audit import AuditService

    factory = ctx["session_factory"]
    async with factory() as session:
        change = await session.get(ConfigChange, uuid.UUID(change_id))
        if change is None:
            return "missing"
        device = await session.get(Device, change.device_id)
        if device is None:
            return "missing-device"
        client = OpnsenseClient(
            device.base_url,
            crypto.decrypt(device.api_key_enc),
            crypto.decrypt(device.api_secret_enc),
            verify_tls=device.verify_tls,
        )
        status = await apply_change(session, change, client, now=datetime.now(timezone.utc))
        await AuditService(session).record(
            actor_user_id=change.created_by, tenant_id=change.tenant_id,
            action="config.change.apply", target_type="config_change",
            target_id=str(change.id), ip=None, details={"status": status},
        )
        await session.commit()
        await ctx["redis"].enqueue_job("backup_device_config", str(change.device_id))  # refresh snapshot
        return status
```
Add `apply_config_change` to `WorkerSettings.functions` (keep all existing). No new cron.
In `tests/test_worker_config.py` add: `apply_config_change in WorkerSettings.functions`.

- [ ] **Step 6: Run + commit**

`... pytest tests/test_config_push_apply.py tests/test_worker_config.py -v` → PASS. Whole suite green.
```bash
git add app/services/config_push.py app/core/queue.py app/worker.py \
        tests/test_config_push_apply.py tests/test_worker_config.py
git commit -m "feat(backend): config-change apply job (staleness guard, advisory lock, dry-run, audit)"
```

---

## Task 5: API (create / preview / schedule / list / cancel) + isolation

**Files:**
- Modify: `app/schemas/config.py`, `app/repositories/config_snapshot.py` (or new `app/repositories/config_change.py`), `app/api/config.py`
- Create: `tests/test_config_push_api.py`, `tests/test_config_push_rls_api.py`

- [ ] **Step 1: Schemas**

In `app/schemas/config.py` add:
```python
class ConfigChangeIn(BaseModel):
    kind: str
    operation: str
    target: str = ""
    payload: dict = {}


class ScheduleIn(BaseModel):
    scheduled_at: datetime | None = None  # None = immediate


class ConfigChangeOut(BaseModel):
    id: uuid.UUID
    device_id: uuid.UUID
    kind: str
    operation: str
    target: str
    status: str
    scheduled_at: datetime | None
    applied_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
```
(Note: `ConfigChangeOut` omits `payload`/`result`/`baseline_hash` to avoid leaking change internals;
a detail endpoint can expose `result` later. Preview returns the `ChangePreview` dict.)

- [ ] **Step 2: Repository**

Create `app/repositories/config_change.py` — `ConfigChangeRepository(session, tenant_id)` with `list(device_id)` (newest-first, tenant-filtered) and `get(change_id)` (tenant-filtered). Mirror `ConfigSnapshotRepository`.

- [ ] **Step 3: Endpoints**

In `app/api/config.py` add the endpoints (CSRF via `enforce_csrf` on the mutating ones, audit, RBAC). `create`/`list`/`preview` gated by `DEVICE_VIEW`; `schedule`/`cancel` by **`CONFIG_PUSH`**.
```python
# imports: enforce_csrf, AuditService, create_change, preview_change, get_enqueuer, ConfigChangeRepository, schemas, Request
@router.post("/devices/{device_id}/config/changes", response_model=ConfigChangeOut,
             status_code=201, dependencies=[Depends(enforce_csrf)])
async def create_config_change(tenant_id, device_id, payload: ConfigChangeIn, request: Request,
        ctx=Depends(require_tenant(Action.CONFIG_PUSH)), session=Depends(get_session)):
    change = await create_change(session, tenant_id=tenant_id, device_id=device_id,
        created_by=ctx.user.id, kind=payload.kind, operation=payload.operation,
        target=payload.target, payload=payload.payload)
    await AuditService(session).record(actor_user_id=ctx.user.id, tenant_id=tenant_id,
        action="config.change.create", target_type="config_change", target_id=str(change.id),
        ip=request.client.host if request.client else None, details={"kind": change.kind, "op": change.operation})
    await session.commit()
    return change

@router.get("/devices/{device_id}/config/changes", response_model=list[ConfigChangeOut])
async def list_config_changes(tenant_id, device_id, ctx=Depends(require_tenant(Action.DEVICE_VIEW)), session=Depends(get_session)):
    return list(await ConfigChangeRepository(session, tenant_id).list(device_id))

@router.get("/devices/{device_id}/config/changes/{change_id}/preview")
async def preview_config_change(tenant_id, device_id, change_id, ctx=Depends(require_tenant(Action.DEVICE_VIEW)), session=Depends(get_session)) -> dict:
    change = await ConfigChangeRepository(session, tenant_id).get(change_id)
    if change is None or change.device_id != device_id:
        raise HTTPException(404, "Change not found")
    return preview_change(change)

@router.post("/devices/{device_id}/config/changes/{change_id}/schedule", response_model=ConfigChangeOut,
             dependencies=[Depends(enforce_csrf)])
async def schedule_config_change(tenant_id, device_id, change_id, body: ScheduleIn, request: Request,
        ctx=Depends(require_tenant(Action.CONFIG_PUSH)), session=Depends(get_session), enqueue=Depends(get_enqueuer)):
    repo = ConfigChangeRepository(session, tenant_id)
    change = await repo.get(change_id)
    if change is None or change.device_id != device_id:
        raise HTTPException(404, "Change not found")
    if change.status not in ("draft", "scheduled"):
        raise HTTPException(409, f"Cannot schedule a change in status {change.status}")
    change.status = "scheduled"
    change.scheduled_at = body.scheduled_at
    await session.flush()
    await AuditService(session).record(actor_user_id=ctx.user.id, tenant_id=tenant_id,
        action="config.change.schedule", target_type="config_change", target_id=str(change.id),
        ip=request.client.host if request.client else None,
        details={"scheduled_at": body.scheduled_at.isoformat() if body.scheduled_at else "immediate"})
    await session.commit()
    await enqueue("apply_config_change", str(change.id), defer_until=body.scheduled_at)
    return change

@router.post("/devices/{device_id}/config/changes/{change_id}/cancel", response_model=ConfigChangeOut,
             dependencies=[Depends(enforce_csrf)])
async def cancel_config_change(tenant_id, device_id, change_id, request: Request,
        ctx=Depends(require_tenant(Action.CONFIG_PUSH)), session=Depends(get_session)):
    repo = ConfigChangeRepository(session, tenant_id)
    change = await repo.get(change_id)
    if change is None or change.device_id != device_id:
        raise HTTPException(404, "Change not found")
    if change.status not in ("draft", "scheduled"):
        raise HTTPException(409, f"Cannot cancel a change in status {change.status}")
    change.status = "cancelled"
    await session.flush()
    await AuditService(session).record(actor_user_id=ctx.user.id, tenant_id=tenant_id,
        action="config.change.cancel", target_type="config_change", target_id=str(change.id),
        ip=request.client.host if request.client else None, details={})
    await session.commit()
    return change
```
Fill the function signatures with the proper FastAPI types (`tenant_id: uuid.UUID`, etc.) as in the existing endpoints.

- [ ] **Step 4: Tests**

Create `tests/test_config_push_api.py` (owner client): create a change → 201; preview → secret-safe summary; schedule immediate → status `scheduled` + the injected enqueuer recorded the job (override `get_enqueuer` with a fake recorder, like `get_prober` is overridden); schedule deferred → `scheduled_at` set + enqueue called with `defer_until`; cancel → `cancelled`; list shows the change; 403 for a `read_only` user on schedule (CONFIG_PUSH denied); 401 without session. **Assert no `payload`/`result`/`baseline_hash` in `ConfigChangeOut` JSON.**
Create `tests/test_config_push_rls_api.py` (real `opngms_app`): tenant A cannot see/preview tenant B's change (404); raw-SQL RLS proof.

Override the enqueuer in tests:
```python
from app.core.queue import get_enqueuer
calls = []
async def _fake_enqueue(name, *args, defer_until=None): calls.append((name, args, defer_until))
app.dependency_overrides[get_enqueuer] = lambda: _fake_enqueue
```

- [ ] **Step 5: Run + alembic check** — whole suite green; `alembic check` clean.

- [ ] **Step 6: Commit**
```bash
git add app/schemas/config.py app/repositories/config_change.py app/api/config.py \
        tests/test_config_push_api.py tests/test_config_push_rls_api.py
git commit -m "feat(backend): config change API (create/preview/schedule/list/cancel, CONFIG_PUSH + RLS)"
```

---

## Task 6: Technical debt

- [ ] **Step 1: Record the 4D-a debt**

Append to this plan:
```markdown
## Technical debt (4D-a)

- **Real push gated off**: `apply_alias` is dry-run only; 4D-b verifies the OPNsense alias endpoints
  against a real device and flips `dry_run` off behind a config/flag.
- **OPNsense alias endpoints TO VERIFY**: `firewall/alias/{add,set,del}Item` + `reconfigure` + payload.
- **Preview is local-only**: shows operation + payload, not the current alias value nor a device-side
  validation; enrich from the snapshot / device dry-run in 4D-b.
- **Enqueuer opens a pool per call**: `app/core/queue.py` creates+closes an ARQ pool each enqueue;
  cache a singleton pool (app lifespan) if volume grows.
- **Advisory lock auto-released at commit**: the apply runs in one transaction; if apply work spans
  multiple commits later, switch to a session-level lock + explicit unlock.
- **Write-only secrets not yet exercised**: aliases carry no secrets; secret-bearing kinds (later)
  must encrypt sensitive payload fields + "leave blank to keep" on apply.
- **No rollback/undo**: a prior snapshot exists for manual restore; an automated undo is a later feature.
- **`ConfigChangeOut` hides payload/result**: a detail endpoint exposing the (secret-safe) result is a
  later nicety.
```

- [ ] **Step 2: Commit**
```bash
git add docs/superpowers/plans/2026-06-09-opngms-phase4-milestone4Da-config-push.md
git commit -m "docs: technical debt milestone 4D-a"
```

---

## Definition of "Done" (4D-a)
- An operator (`CONFIG_PUSH`) can create an alias change, preview it (secret-safe), and schedule it immediately or for a future date/time (ARQ deferred).
- The apply job takes a per-device advisory lock, re-checks `canonical_hash` (staleness → `conflict`, no clobber), runs the **dry-run** apply (no real mutation), audits every step, and enqueues a snapshot refresh.
- Everything is tenant-scoped + RLS-isolated and gated by `CONFIG_PUSH`; isolation/RBAC proven by tests.
- Suite green + `alembic check` clean.

---

## Technical debt (4D-a) — consolidated from reviews

- **Real push gated off**: `apply_alias` is dry-run only; **4D-b** verifies the OPNsense alias endpoints
  against a real device and flips `dry_run` off behind a config/flag.
- **OPNsense alias endpoints TO VERIFY**: `firewall/alias/{add,set,del}Item` + `reconfigure` + payload.
- **Preview is local-only**: shows operation + payload, not the current alias value nor a device-side
  validation; enrich from the snapshot / device dry-run in 4D-b.
- **Enqueuer opens a pool per call** (`app/core/queue.py`): cache a singleton ARQ pool (app lifespan)
  if volume grows.
- **Advisory lock auto-released at commit**: the apply runs in one transaction; if apply work later
  spans multiple commits, switch to a session-level lock + explicit unlock.
- **Write-only secrets not yet exercised**: aliases carry no secrets; secret-bearing kinds (4D-d)
  must encrypt sensitive payload fields + "leave blank to keep" on apply.
- **No rollback/undo**: a prior snapshot exists for manual restore; an automated undo is a later feature.
- **`ConfigChangeOut` hides payload/result**: a detail endpoint exposing the (secret-safe) result is a
  later nicety.
- **`ApiError(0, ...)` for an unknown alias operation** (connector): a status_code of 0 is semantically
  odd but pragmatically useful (caught as `OpnsenseError` by the apply job → `failed`, no crash). The
  schema now also rejects bad operations with `Literal` 422 at create time.
- *(Resolved in review)* Cross-tenant `create` is now rejected (404 — device not visible under the
  tenant's RLS), closing a latent cross-tenant push escalation.
