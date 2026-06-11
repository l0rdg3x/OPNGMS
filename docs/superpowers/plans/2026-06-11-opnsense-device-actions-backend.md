# OPNsense Device Actions — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backend for triggering four OPNsense device actions — firmware package update, major release upgrade, plugin install, plugin remove — each now or scheduled, run by a reboot-tolerant worker, tracked in a `firmware_actions` table.

**Architecture:** New connector action methods (POST firmware ops + GET upgradestatus); a `firmware_actions` record + the existing `defer_until` scheduling; one worker `run_firmware_action` that POSTs the op and polls `upgradestatus` to completion (tolerating reboots), with a multi-step loop for `firmware_upgrade` and an up-to-date precondition for `plugin_install`. No master switch (UI confirmation is the gate, added in the frontend plan).

**Tech Stack:** Python 3.14, SSRF-guarded connector, SQLAlchemy/Alembic (RLS), ARQ worker, pytest + respx.

**Spec:** `docs/superpowers/specs/2026-06-11-opnsense-device-actions-design.md`
**Branch:** `feat/opnsense-device-actions` (created; spec committed there).
**Scope:** Backend only. The frontend (firmware/plugins UI + WebGUI button) is a separate plan after this merges.

**Run tests:** `cd /home/l0rdg3x/coding/OPNGMS/backend && .venv/bin/python -m pytest <files> -q`. DB tests need env `TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test`. English; commit trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File Structure

- **Create:** `backend/app/models/firmware_action.py`, `backend/migrations/versions/0018_firmware_actions.py`, `backend/app/services/firmware_action.py` (worker body + poll/helpers), `backend/app/schemas/firmware.py`, `backend/app/api/firmware.py`, `scripts/verify_plugin_live.py`, tests (`test_connector_firmware.py`, `test_migration_0018.py`, `test_firmware_action_service.py`, `test_firmware_api.py`).
- **Modify:** `backend/app/connectors/opnsense/client.py` (action methods), `backend/app/worker.py` (`run_firmware_action` job + register in `WorkerSettings.functions`), `backend/app/main.py` (include the firmware router).

---

## Task 1: Connector firmware-action methods

**Files:** Modify `backend/app/connectors/opnsense/client.py`; Create `backend/tests/test_connector_firmware.py`.

**Context:** `client.py` already has `_post(path, json, timeout=None)`, `_get(path)`, `RECONFIGURE_TIMEOUT = 120.0`, `ApiError`. These firmware ops are slow → use the long timeout. Plugin install/remove put the plugin name in the URL path → validate the name first (no injection).

- [ ] **Step 1: Write `backend/tests/test_connector_firmware.py`:**
```python
import httpx
import pytest
import respx

from app.connectors.opnsense.client import ApiError, OpnsenseClient


def _client():
    return OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)


@respx.mock
async def test_firmware_check_and_status():
    chk = respx.post(url__regex=r".*/api/core/firmware/check.*").mock(
        return_value=httpx.Response(200, json={"status": "ok"}))
    stt = respx.get(url__regex=r".*/api/core/firmware/status.*").mock(
        return_value=httpx.Response(200, json={"status": "ok", "updates": "3", "download_size": "12M",
                                               "upgrade_needs_reboot": "1"}))
    assert (await _client().firmware_check())["status"] == "ok" and chk.called
    st = await _client().firmware_status_raw()
    assert st["updates"] == "3" and stt.called


@respx.mock
async def test_firmware_update_upgrade_and_status():
    up = respx.post(url__regex=r".*/api/core/firmware/update.*").mock(
        return_value=httpx.Response(200, json={"status": "ok", "msg_uuid": "x"}))
    ug = respx.post(url__regex=r".*/api/core/firmware/upgrade.*").mock(
        return_value=httpx.Response(200, json={"status": "ok"}))
    us = respx.get(url__regex=r".*/api/core/firmware/upgradestatus.*").mock(
        return_value=httpx.Response(200, json={"status": "running", "log": "..."}))
    assert (await _client().firmware_update())["status"] == "ok" and up.called
    assert (await _client().firmware_upgrade())["status"] == "ok" and ug.called
    assert (await _client().firmware_upgrade_status())["status"] == "running" and us.called


@respx.mock
async def test_plugin_install_remove_paths():
    ins = respx.post(url__regex=r".*/api/core/firmware/install/os-acme-client.*").mock(
        return_value=httpx.Response(200, json={"status": "ok"}))
    rem = respx.post(url__regex=r".*/api/core/firmware/remove/os-acme-client.*").mock(
        return_value=httpx.Response(200, json={"status": "ok"}))
    assert (await _client().plugin_install("os-acme-client"))["status"] == "ok" and ins.called
    assert (await _client().plugin_remove("os-acme-client"))["status"] == "ok" and rem.called


async def test_plugin_name_validation_rejects_injection():
    with pytest.raises(ApiError):
        await _client().plugin_install("../core/firmware/reboot")
    with pytest.raises(ApiError):
        await _client().plugin_remove("os bad name")
    with pytest.raises(ApiError):
        await _client().plugin_install("")
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/l0rdg3x/coding/OPNGMS/backend && .venv/bin/python -m pytest tests/test_connector_firmware.py -q`
Expected: FAIL (AttributeError: no `firmware_check`).

- [ ] **Step 3: Add the methods to `client.py`** — `re` is NOT currently imported in `client.py` (top imports are `asyncio`, `ssl`, `httpx`), so add `import re` to the stdlib import block, then add the module-level regex constant near the other module constants (e.g. next to `RECONFIGURE_TIMEOUT`):
```python
import re  # add to the stdlib imports at the top

_PLUGIN_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")  # module-level, near RECONFIGURE_TIMEOUT
```

Add these methods to the `OpnsenseClient` class (e.g. after `apply_alias`):
```python
    async def firmware_check(self) -> dict:
        """Trigger a firmware mirror check."""
        return await self._post("core/firmware/check", {}, timeout=RECONFIGURE_TIMEOUT)

    async def firmware_status_raw(self) -> dict:
        """Raw core/firmware/status (updates count, download size, reboot-needed, latest major)."""
        return await self._get("core/firmware/status")

    async def firmware_update(self) -> dict:
        """Apply all available package updates (may reboot)."""
        return await self._post("core/firmware/update", {}, timeout=RECONFIGURE_TIMEOUT)

    async def firmware_upgrade(self) -> dict:
        """Major release upgrade (always reboots)."""
        return await self._post("core/firmware/upgrade", {}, timeout=RECONFIGURE_TIMEOUT)

    async def firmware_upgrade_status(self) -> dict:
        """Progress of a running firmware operation: {status, log}."""
        return await self._get("core/firmware/upgradestatus")

    async def plugin_install(self, name: str) -> dict:
        """Install a plugin by exact name (charset-validated to avoid path injection)."""
        return await self._post(f"core/firmware/install/{self._plugin_name(name)}", {}, timeout=RECONFIGURE_TIMEOUT)

    async def plugin_remove(self, name: str) -> dict:
        """Remove a plugin by exact name (charset-validated)."""
        return await self._post(f"core/firmware/remove/{self._plugin_name(name)}", {}, timeout=RECONFIGURE_TIMEOUT)

    @staticmethod
    def _plugin_name(name: str) -> str:
        if not name or not _PLUGIN_NAME_RE.match(name):
            raise ApiError(0, f"invalid plugin name: {name!r}")
        return name
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /home/l0rdg3x/coding/OPNGMS/backend && .venv/bin/python -m pytest tests/test_connector_firmware.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/connectors/opnsense/client.py backend/tests/test_connector_firmware.py
git commit -m "feat(opnsense): connector firmware action methods (update/upgrade/plugin install-remove)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `firmware_actions` model + migration 0018 (RLS)

**Files:** Create `backend/app/models/firmware_action.py`, `backend/migrations/versions/0018_firmware_actions.py`, `backend/tests/test_migration_0018.py`.

- [ ] **Step 1: Create the model** `backend/app/models/firmware_action.py`:
```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class FirmwareAction(UUIDPKMixin, Base):
    __tablename__ = "firmware_actions"
    __table_args__ = (
        Index("ix_firmware_actions_tenant_device_created", "tenant_id", "device_id", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String)            # firmware_update|firmware_upgrade|plugin_install|plugin_remove
    target: Mapped[str] = mapped_column(String, default="", server_default="")  # plugin name; "" for firmware
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    status: Mapped[str] = mapped_column(String, default="scheduled", server_default="scheduled")
    result: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 2: Register the model so `Base.metadata` + Alembic see it** — confirm `app/models/__init__.py` imports the models package eagerly. Add an import if the package lists models explicitly:

Run: `cd /home/l0rdg3x/coding/OPNGMS/backend && rg -n "config_change|FirmwareAction|import" app/models/__init__.py | head`
If `app/models/__init__.py` imports each model (e.g. `from app.models.config_change import ConfigChange`), add `from app.models.firmware_action import FirmwareAction  # noqa: F401`. If it imports nothing explicit (relies on metadata via the migration), skip.

- [ ] **Step 3: Create the migration** `backend/migrations/versions/0018_firmware_actions.py` (mirrors `0010_config_changes.py`'s RLS pattern). First confirm the head:

Run: `cd /home/l0rdg3x/coding/OPNGMS/backend && grep -E "^revision" migrations/versions/0017_config_change_pre_apply_snapshot.py`
Expected `revision = "0017"`. Then:
```python
"""firmware_actions table + RLS"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.db_roles import APP_ROLE, grant_app_role_statements
from app.core.rls import POLICY_NAME, policy_create_statement

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "firmware_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=False, server_default=""),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="scheduled"),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_firmware_actions_tenant_id", "firmware_actions", ["tenant_id"])
    op.create_index("ix_firmware_actions_device_id", "firmware_actions", ["device_id"])
    op.create_index("ix_firmware_actions_tenant_device_created",
                    "firmware_actions", ["tenant_id", "device_id", "created_at"])
    op.execute("ALTER TABLE firmware_actions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE firmware_actions FORCE ROW LEVEL SECURITY")
    op.execute(policy_create_statement("firmware_actions"))
    for stmt in grant_app_role_statements():
        op.execute(stmt)


def downgrade() -> None:
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON firmware_actions FROM {APP_ROLE}")
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON firmware_actions")
    op.execute("ALTER TABLE firmware_actions NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE firmware_actions DISABLE ROW LEVEL SECURITY")
    op.drop_table("firmware_actions")
```
(`grant_app_role_statements()` grants on ALL tables incl. the new one — same as 0010.)

- [ ] **Step 4: Create `backend/tests/test_migration_0018.py`:**
```python
from sqlalchemy import text


async def test_firmware_actions_table_exists(db_engine):
    async with db_engine.connect() as conn:
        tables = (await conn.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_name='firmware_actions'"
        ))).scalars().all()
    assert "firmware_actions" in tables
```

- [ ] **Step 5: Offline + DB verification.** Offline: `cd backend && .venv/bin/python -c "from app.models.firmware_action import FirmwareAction; print([c.name for c in FirmwareAction.__table__.columns])"` and the revision-chain check (`revision 0018 down_revision 0017`). The orchestrator runs the migration test + `alembic upgrade head`. Report the offline outputs.

- [ ] **Step 6: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/models/firmware_action.py backend/migrations/versions/0018_firmware_actions.py backend/tests/test_migration_0018.py backend/app/models/__init__.py
git commit -m "feat(firmware): firmware_actions table + RLS (migration 0018)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```
(Drop `app/models/__init__.py` from the add if you did not modify it.)

---

## Task 3: Worker service `firmware_action.py` (reboot-tolerant runner)

**Files:** Create `backend/app/services/firmware_action.py`, `backend/tests/test_firmware_action_service.py`.

**Context:** `run_firmware_action(session, action, client, now)` is the service entry (the worker job builds the client + calls it). It dispatches by `kind`, polls `upgradestatus` reboot-tolerantly, loops for `firmware_upgrade`, and pre-checks for `plugin_install`. Polling sleeps are via `asyncio.sleep` (tests monkeypatch it to a no-op). `OpnsenseError`/`ReachabilityError` come from `app.connectors.opnsense.client`. Reuse `config_push._advisory_key` for the per-device lock.

- [ ] **Step 1: Write `backend/tests/test_firmware_action_service.py`:**
```python
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.connectors.opnsense.client import ReachabilityError
from app.models.firmware_action import FirmwareAction
from app.services.firmware_action import run_firmware_action


class FakeClient:
    """Scriptable client. `status_seq` feeds firmware_upgrade_status; `reach_fail` makes the next
    N status polls raise ReachabilityError (a reboot) before test_connection succeeds."""
    def __init__(self, *, check=None, status=None, status_seq=None, reach_fail=0):
        self._check = check or {"status": "none"}
        self._status = status or {"status": "none", "updates": "0"}
        self._status_seq = list(status_seq or [{"status": "done"}])
        self._reach_fail = reach_fail
        self.calls = []

    async def firmware_check(self): self.calls.append("check"); return self._check
    async def firmware_status_raw(self): return self._status
    async def firmware_update(self): self.calls.append("update"); return {"status": "ok"}
    async def firmware_upgrade(self): self.calls.append("upgrade"); return {"status": "ok"}
    async def plugin_install(self, name): self.calls.append(f"install:{name}"); return {"status": "ok"}
    async def plugin_remove(self, name): self.calls.append(f"remove:{name}"); return {"status": "ok"}
    async def test_connection(self):
        if self._reach_fail > 0:
            self._reach_fail -= 1
            raise ReachabilityError("rebooting")
        return "26.1.9"
    async def get_device_identity(self):
        from app.connectors.opnsense.identity import DeviceIdentity
        return DeviceIdentity(edition="community", version="26.1.9", series="26.1")
    def set_identity(self, e, v): pass
    async def firmware_upgrade_status(self):
        if self._reach_fail > 0:
            self._reach_fail -= 1
            raise ReachabilityError("rebooting")
        return self._status_seq.pop(0) if len(self._status_seq) > 1 else self._status_seq[0]


async def _action(db_engine, tenant_id, kind, target="", status="scheduled") -> uuid.UUID:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did, aid = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        await s.execute(text(
            "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tenant_id})
        await s.execute(text(
            "INSERT INTO firmware_actions (id, tenant_id, device_id, created_by, kind, target, status) "
            "VALUES (:i,:t,:d,:u,:k,:g,:st)"),
            {"i": aid, "t": tenant_id, "d": did, "u": uuid.uuid4(), "k": kind, "g": target, "st": status})
        await s.commit()
    return aid


async def _run(db_engine, aid, client):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        act = await s.get(FirmwareAction, aid)
        st = await run_firmware_action(s, act, client, now=datetime.now(timezone.utc))
        await s.commit()
    async with factory() as s:
        return st, await s.get(FirmwareAction, aid)


async def test_plugin_remove_runs(db_engine, two_tenants, monkeypatch):
    import app.services.firmware_action as fa
    monkeypatch.setattr(fa.asyncio, "sleep", lambda *a, **k: _noop())
    ta, _ = two_tenants
    aid = await _action(db_engine, ta, "plugin_remove", target="os-acme-client")
    client = FakeClient(status_seq=[{"status": "done"}])
    st, act = await _run(db_engine, aid, client)
    assert st == "done" and act.status == "done"
    assert "remove:os-acme-client" in client.calls


async def test_plugin_install_blocked_when_updates_pending(db_engine, two_tenants, monkeypatch):
    import app.services.firmware_action as fa
    monkeypatch.setattr(fa.asyncio, "sleep", lambda *a, **k: _noop())
    ta, _ = two_tenants
    aid = await _action(db_engine, ta, "plugin_install", target="os-acme-client")
    client = FakeClient(status={"status": "ok", "updates": "3"})  # updates pending
    st, act = await _run(db_engine, aid, client)
    assert st == "failed" and "up to date" in act.result.get("error", "")
    assert not any(c.startswith("install:") for c in client.calls)  # NO install


async def test_firmware_update_reboot_tolerant(db_engine, two_tenants, monkeypatch):
    import app.services.firmware_action as fa
    monkeypatch.setattr(fa.asyncio, "sleep", lambda *a, **k: _noop())
    ta, _ = two_tenants
    aid = await _action(db_engine, ta, "firmware_update")
    # status poll raises ReachabilityError twice (reboot) then test_connection comes back, then done
    client = FakeClient(reach_fail=2, status_seq=[{"status": "done"}])
    st, act = await _run(db_engine, aid, client)
    assert st == "done" and "update" in client.calls


async def test_firmware_upgrade_multistep_loop(db_engine, two_tenants, monkeypatch):
    import app.services.firmware_action as fa
    monkeypatch.setattr(fa.asyncio, "sleep", lambda *a, **k: _noop())
    ta, _ = two_tenants
    aid = await _action(db_engine, ta, "firmware_upgrade")

    # check returns "updates available" for 2 iterations, then "up to date"
    class UpgradeClient(FakeClient):
        def __init__(self):
            super().__init__(status_seq=[{"status": "done"}])
            self._checks = [{"status": "ok"}, {"status": "ok"}, {"status": "none"}]
        async def firmware_status_raw(self):
            return self._checks.pop(0) if len(self._checks) > 1 else self._checks[0]

    client = UpgradeClient()
    st, act = await _run(db_engine, aid, client)
    assert st == "done"
    assert client.calls.count("update") + client.calls.count("upgrade") == 2  # two steps then converged


def _noop():
    async def _a(): return None
    return _a()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/l0rdg3x/coding/OPNGMS/backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest tests/test_firmware_action_service.py -q`
Expected: FAIL (ModuleNotFoundError: firmware_action).

- [ ] **Step 3: Implement `backend/app/services/firmware_action.py`:**
```python
"""Run a scheduled/now firmware action against a device: update, upgrade, plugin install/remove.

Reboot-tolerant: the device going unreachable during a reboot is expected; only exceeding the
poll budget marks the action failed. A major upgrade runs as a multi-step loop (update/upgrade
then reboot, repeated until the device reports up to date). Plugin install is refused unless the
firmware is up to date (OPNsense pins the plugin repo to the running firmware)."""
import asyncio
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense import parsers
from app.connectors.opnsense.client import OpnsenseError, ReachabilityError
from app.models.firmware_action import FirmwareAction
from app.services.config_push import _advisory_key

MAX_UPGRADE_STEPS = 6
MAX_STATUS_POLLS = 360       # ~30 min at POLL_INTERVAL
REBOOT_MAX_POLLS = 180       # ~15 min waiting for the box to come back
POLL_INTERVAL = 5.0


def _to_int(v) -> int:
    try:
        return int(str(v).strip())
    except (ValueError, TypeError):
        return 0


def _updates_pending(status: dict) -> bool:
    """firmware/status: status 'ok' means upgrades available; a positive `updates` count too."""
    status = status or {}
    return str(status.get("status", "")).lower() == "ok" or _to_int(status.get("updates")) > 0


def _major_offered(status: dict) -> bool:
    """A newer MAJOR (different series) is offered."""
    status = status or {}
    cur = status.get("product_version") or (status.get("product") or {}).get("product_version", "")
    latest = status.get("product_latest") or (status.get("product") or {}).get("product_latest", "")
    return bool(latest) and parsers.parse_version(latest) > parsers.parse_version(cur) \
        and parsers.series_of(latest) != parsers.series_of(cur)


async def _wait_until_reachable(client) -> None:
    for _ in range(REBOOT_MAX_POLLS):
        try:
            await client.test_connection()
            return
        except OpnsenseError:
            await asyncio.sleep(POLL_INTERVAL)
    raise OpnsenseError("device did not come back after reboot within budget")


async def _poll_until_done(client) -> dict:
    """Poll upgradestatus until the running op finishes; tolerate a reboot (unreachable -> back)."""
    for _ in range(MAX_STATUS_POLLS):
        try:
            st = await client.firmware_upgrade_status()
        except ReachabilityError:
            await _wait_until_reachable(client)
            continue
        if str(st.get("status", "")).lower() != "running":
            return st
        await asyncio.sleep(POLL_INTERVAL)
    raise OpnsenseError("firmware operation did not complete within budget")


async def run_firmware_action(session: AsyncSession, action: FirmwareAction, client, now: datetime) -> str:
    """Execute a firmware action. Returns the new status. Per-device serialized."""
    if action.status not in ("scheduled", "running"):
        return action.status
    got = (await session.execute(
        text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": _advisory_key(action.device_id)}
    )).scalar_one()
    if not got:
        return action.status  # another action holds the device lock; leave scheduled for retry
    action.status = "running"
    await session.flush()
    try:
        if action.kind == "plugin_remove":
            await client.plugin_remove(action.target)
            await _poll_until_done(client)
        elif action.kind == "plugin_install":
            await client.firmware_check()
            if _updates_pending(await client.firmware_status_raw()):
                action.status = "failed"
                action.result = {"error": "device must be up to date before installing plugins"}
                await session.flush()
                return "failed"
            await client.plugin_install(action.target)
            await _poll_until_done(client)
        elif action.kind == "firmware_update":
            await client.firmware_update()
            await _poll_until_done(client)
        elif action.kind == "firmware_upgrade":
            steps = 0
            for _ in range(MAX_UPGRADE_STEPS):
                await client.firmware_check()
                st = await client.firmware_status_raw()
                if not _updates_pending(st) and not _major_offered(st):
                    break
                if _major_offered(st):
                    await client.firmware_upgrade()
                else:
                    await client.firmware_update()
                await _poll_until_done(client)
                steps += 1
            else:
                raise OpnsenseError("upgrade did not converge within MAX_UPGRADE_STEPS")
            action.result = {"steps": steps}
        else:
            action.status = "failed"
            action.result = {"error": f"unknown action kind: {action.kind}"}
            await session.flush()
            return "failed"
        ident = await client.get_device_identity()
        action.status = "done"
        action.applied_at = now
        action.result = {**(action.result or {}), "version": ident.version}
    except OpnsenseError:
        action.status = "failed"
        action.result = {"error": "action failed"}
    await session.flush()
    return action.status
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /home/l0rdg3x/coding/OPNGMS/backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest tests/test_firmware_action_service.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/services/firmware_action.py backend/tests/test_firmware_action_service.py
git commit -m "feat(firmware): reboot-tolerant action runner (update/upgrade loop, plugin precheck)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Worker job + API endpoints

**Files:** Create `backend/app/schemas/firmware.py`, `backend/app/api/firmware.py`, `backend/tests/test_firmware_api.py`; Modify `backend/app/worker.py`, `backend/app/main.py`.

- [ ] **Step 1: Create the schemas** `backend/app/schemas/firmware.py`:
```python
import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator

_KINDS = {"firmware_update", "firmware_upgrade", "plugin_install", "plugin_remove"}


class FirmwareActionIn(BaseModel):
    kind: str
    target: str = ""
    scheduled_at: datetime | None = None

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in _KINDS:
            raise ValueError(f"invalid kind: {v}")
        return v


class FirmwareActionOut(BaseModel):
    id: uuid.UUID
    kind: str
    target: str
    status: str
    scheduled_at: datetime | None
    applied_at: datetime | None
    result: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class FirmwareCheckOut(BaseModel):
    status: str
    updates: int
    download_size: str
    needs_reboot: bool
    new_major: bool
```

- [ ] **Step 2: Write `backend/tests/test_firmware_api.py`** (mirrors how other API tests log in + call; uses the `api_client`/`db_engine` fixtures and the existing auth helpers):
```python
import uuid

import respx
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.conftest import csrf_headers


async def _device(db_engine, tenant_id) -> uuid.UUID:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    async with factory() as s:
        await s.execute(text(
            "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
            "VALUES (:i,:t,'fw','https://10.0.0.9',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tenant_id})
        await s.commit()
    return did


async def test_create_action_enqueues(api_client, db_engine, tenant_admin_login):
    tenant_id, client = tenant_admin_login
    did = await _device(db_engine, tenant_id)
    enqueued = []
    # The enqueuer is overridden in tests; patch it to capture (see conftest get_enqueuer override).
    r = await client.post(
        f"/api/tenants/{tenant_id}/devices/{did}/firmware/action",
        json={"kind": "firmware_update"}, headers=csrf_headers(client))
    assert r.status_code in (200, 201)
    body = r.json()
    assert body["kind"] == "firmware_update" and body["status"] == "scheduled"


async def test_create_action_rejects_bad_kind(api_client, db_engine, tenant_admin_login):
    tenant_id, client = tenant_admin_login
    did = await _device(db_engine, tenant_id)
    r = await client.post(f"/api/tenants/{tenant_id}/devices/{did}/firmware/action",
                          json={"kind": "reboot_now"}, headers=csrf_headers(client))
    assert r.status_code == 422


async def test_plugin_action_requires_target(api_client, db_engine, tenant_admin_login):
    tenant_id, client = tenant_admin_login
    did = await _device(db_engine, tenant_id)
    r = await client.post(f"/api/tenants/{tenant_id}/devices/{did}/firmware/action",
                          json={"kind": "plugin_install", "target": ""}, headers=csrf_headers(client))
    assert r.status_code == 400
```
NOTE: this task depends on the test fixtures that already exist for the config-push API tests (`api_client`, `tenant_admin_login`/login helper, `csrf_headers`, the `get_enqueuer` override). Before writing, read `backend/tests/test_config_push_api.py` (or `test_config_push_rls_api.py`) to copy the EXACT login fixture names + enqueuer-capture pattern this codebase uses, and adapt the three tests above to match. Do not invent fixture names.

- [ ] **Step 3: Run to verify failure**

Run: `cd /home/l0rdg3x/coding/OPNGMS/backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest tests/test_firmware_api.py -q`
Expected: FAIL (404 — router not wired).

- [ ] **Step 4: Create `backend/app/api/firmware.py`:**
```python
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense.client import OpnsenseClient, OpnsenseError
from app.core import crypto
from app.core.db import get_session
from app.core.deps import TenantContext, enforce_csrf, require_tenant
from app.core.queue import get_enqueuer
from app.core.rbac import Action
from app.models.device import Device
from app.models.firmware_action import FirmwareAction
from app.schemas.firmware import FirmwareActionIn, FirmwareActionOut, FirmwareCheckOut
from app.services.firmware_action import _to_int, _major_offered

router = APIRouter(prefix="/api/tenants/{tenant_id}", tags=["firmware"])


async def _device_or_404(session: AsyncSession, tenant_id: uuid.UUID, device_id: uuid.UUID) -> Device:
    device = await session.get(Device, device_id)
    if device is None or device.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


@router.post("/devices/{device_id}/firmware/check", response_model=FirmwareCheckOut)
async def firmware_check(
    tenant_id: uuid.UUID, device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
    _: None = Depends(enforce_csrf),
) -> FirmwareCheckOut:
    device = await _device_or_404(session, tenant_id, device_id)
    client = OpnsenseClient(device.base_url, crypto.decrypt(device.api_key_enc),
                            crypto.decrypt(device.api_secret_enc), verify_tls=device.verify_tls,
                            tls_fingerprint=device.tls_fingerprint)
    try:
        await client.firmware_check()
        st = await client.firmware_status_raw()
    except OpnsenseError as exc:
        raise HTTPException(status_code=502, detail=type(exc).__name__) from exc
    return FirmwareCheckOut(
        status=str(st.get("status", "")), updates=_to_int(st.get("updates")),
        download_size=str(st.get("download_size", "")),
        needs_reboot=str(st.get("upgrade_needs_reboot", "")) in ("1", "true", "True"),
        new_major=_major_offered(st),
    )


@router.post("/devices/{device_id}/firmware/action", response_model=FirmwareActionOut)
async def create_firmware_action(
    tenant_id: uuid.UUID, device_id: uuid.UUID, body: FirmwareActionIn,
    ctx: TenantContext = Depends(require_tenant(Action.CONFIG_PUSH)),
    session: AsyncSession = Depends(get_session),
    enqueue=Depends(get_enqueuer),
    _: None = Depends(enforce_csrf),
) -> FirmwareActionOut:
    await _device_or_404(session, tenant_id, device_id)
    if body.kind in ("plugin_install", "plugin_remove") and not body.target:
        raise HTTPException(status_code=400, detail="target (plugin name) required")
    if body.kind in ("firmware_update", "firmware_upgrade") and body.target:
        raise HTTPException(status_code=400, detail="target not allowed for firmware actions")
    action = FirmwareAction(
        tenant_id=tenant_id, device_id=device_id, created_by=ctx.user_id,
        kind=body.kind, target=body.target, scheduled_at=body.scheduled_at, status="scheduled",
    )
    session.add(action)
    await session.flush()
    await enqueue("run_firmware_action", str(action.id), defer_until=body.scheduled_at)
    await session.commit()
    return FirmwareActionOut.model_validate(action)


@router.get("/devices/{device_id}/firmware/actions", response_model=list[FirmwareActionOut])
async def list_firmware_actions(
    tenant_id: uuid.UUID, device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> list[FirmwareActionOut]:
    await _device_or_404(session, tenant_id, device_id)
    rows = (await session.execute(
        select(FirmwareAction).where(FirmwareAction.device_id == device_id)
        .order_by(FirmwareAction.created_at.desc()).limit(50)
    )).scalars().all()
    return [FirmwareActionOut.model_validate(r) for r in rows]
```
IMPORTANT: confirm the exact names of `ctx.user_id` and the CSRF/enqueuer/RBAC deps by reading `app/api/config.py`'s `schedule_config_change` + `create_config_change` (use whatever `ctx.<attr>` that file uses for the acting user id, and the same `get_enqueuer`/`enforce_csrf` wiring). Adjust if they differ.

- [ ] **Step 5: Wire the router** — in `backend/app/main.py`, find where the config router is included (`app.include_router(config.router)` or similar) and add the firmware router the same way:
```python
from app.api import firmware
app.include_router(firmware.router)
```
(Match the existing import + include style in `main.py`.)

- [ ] **Step 6: Add the worker job** — in `backend/app/worker.py`, add the job (mirroring `apply_config_change`) and register it:
```python
async def run_firmware_action(ctx: dict, action_id: str) -> str:
    """Job: run a scheduled/now firmware action against a device."""
    from app.models.firmware_action import FirmwareAction
    from app.services.audit import AuditService
    from app.services.firmware_action import run_firmware_action as _run

    factory = ctx["session_factory"]
    async with factory() as session:
        action = await session.get(FirmwareAction, uuid.UUID(action_id))
        if action is None:
            return "missing"
        device = await session.get(Device, action.device_id)
        if device is None:
            return "missing-device"
        client = OpnsenseClient(
            device.base_url, crypto.decrypt(device.api_key_enc), crypto.decrypt(device.api_secret_enc),
            verify_tls=device.verify_tls, tls_fingerprint=device.tls_fingerprint,
        )
        status = await _run(session, action, client, now=datetime.now(timezone.utc))
        await AuditService(session).record(
            actor_user_id=action.created_by, tenant_id=action.tenant_id,
            action="device.firmware.action", target_type="firmware_action", target_id=str(action.id),
            ip=None, details={"kind": action.kind, "status": status},
        )
        await session.commit()
        return status
```
and add `run_firmware_action` to `WorkerSettings.functions` (the list that already contains `apply_config_change`). (`uuid`, `datetime`, `timezone`, `OpnsenseClient`, `crypto`, `Device` are already imported in `worker.py`.)

- [ ] **Step 7: Run the API tests + a broad import/worker sanity**

Run:
```bash
cd /home/l0rdg3x/coding/OPNGMS/backend
.venv/bin/python -c "import app.worker, app.api.firmware, app.main; print('import OK')"
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest tests/test_firmware_api.py -q
```
Expected: import OK; API tests PASS.

- [ ] **Step 8: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/schemas/firmware.py backend/app/api/firmware.py backend/app/worker.py backend/app/main.py backend/tests/test_firmware_api.py
git commit -m "feat(firmware): API endpoints (check/action/list) + worker job

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Live plugin verify script

**Files:** Create `scripts/verify_plugin_live.py`.

**Context:** A developer tool (NOT in CI) that exercises plugin install+remove against real hardware with a throwaway plugin and guaranteed cleanup. The implementer creates + parse-checks + commits; the orchestrator runs it against the box.

- [ ] **Step 1: Create `scripts/verify_plugin_live.py`:**
```python
#!/usr/bin/env python3
"""Live plugin install/remove check against real OPNsense hardware (NOT in CI).

Usage:
    OPNSENSE_URL=https://192.168.1.82 OPNSENSE_KEYFILE=~/path/apikey.txt \
    python scripts/verify_plugin_live.py [plugin-name]

Installs a small throwaway plugin (default os-acme-client), polls upgradestatus to completion,
confirms it is installed, then removes it (guaranteed cleanup). Requires the device to be up to
date (OPNsense blocks plugin installs otherwise) — it prints a clear message and aborts cleanly
if updates are pending. Credentials are never printed.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.connectors.opnsense.client import OpnsenseClient  # noqa: E402
from app.services.firmware_action import _poll_until_done, _updates_pending  # noqa: E402


def _creds(keyfile: str) -> tuple[str, str]:
    key = secret = ""
    for line in Path(keyfile).expanduser().read_text().splitlines():
        if line.startswith("key="):
            key = line[4:].strip()
        elif line.startswith("secret="):
            secret = line[7:].strip()
    if not key or not secret:
        raise SystemExit("key/secret not found in key file")
    return key, secret


async def _installed(client, name) -> bool:
    info = await client.get_plugin_info()
    return name in info.get("plugins", [])


async def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "os-acme-client"
    base = os.environ["OPNSENSE_URL"]
    key, secret = _creds(os.environ["OPNSENSE_KEYFILE"])
    client = OpnsenseClient(base, key, secret, verify_tls=False)
    await client.firmware_check()
    if _updates_pending(await client.firmware_status_raw()):
        print("ABORT: device has pending firmware updates — OPNsense blocks plugin installs. Update first.")
        return 2
    rc = 1
    try:
        print(f"install {name} ...")
        await client.plugin_install(name)
        await _poll_until_done(client)
        print(f"installed -> {await _installed(client, name)}")
        rc = 0
    finally:
        try:
            print(f"remove {name} ...")
            await client.plugin_remove(name)
            await _poll_until_done(client)
            gone = not await _installed(client, name)
            print(f"cleanup -> removed={gone}")
            if not gone:
                rc = 1
        except Exception as exc:  # noqa: BLE001
            print(f"CLEANUP ERROR: {type(exc).__name__}: {exc}")
            rc = 1
    print("ALL PASS" if rc == 0 else "FAILED" if rc == 1 else "SKIPPED (not up to date)")
    return rc


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

- [ ] **Step 2: Parse-check**

Run: `cd /home/l0rdg3x/coding/OPNGMS && backend/.venv/bin/python -c "import ast; ast.parse(open('scripts/verify_plugin_live.py').read()); print('parse OK')"`
Expected: `parse OK`.

- [ ] **Step 3: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add scripts/verify_plugin_live.py
git commit -m "tools(firmware): live plugin install/remove verify script (throwaway plugin, cleanup)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

> **Orchestrator note:** after this task, the orchestrator runs the script against the real box and confirms `installed -> True`, `cleanup -> removed=True`, `ALL PASS` (or a clean `SKIPPED` if the box has pending updates — in which case run the firmware-update path manually first or accept SKIPPED). This verifies the plugin install/remove + `upgradestatus` polling on real hardware. The firmware update/upgrade paths are verified by the mocked worker tests only (not run against the box — they would reboot it).

---

## Final verification

- [ ] Full backend suite green: `cd backend && TEST_DATABASE_URL=... ADMIN_DATABASE_URL=... .venv/bin/python -m pytest -q`
- [ ] Migration 0018 applies cleanly (`alembic upgrade head`)
- [ ] Live plugin verify (orchestrator) → `ALL PASS` (or clean `SKIPPED`), box left clean
- [ ] Dispatch a final holistic review, then superpowers:finishing-a-development-branch. The frontend (firmware/plugins UI + WebGUI button) is a separate plan/PR.

---

## Self-Review (author)

**Spec coverage (backend portion):** connector methods incl. plugin-name validation (Task 1); `firmware_actions` model + RLS migration (Task 2); the reboot-tolerant runner with the multi-step upgrade loop + plugin-install precondition (Task 3); API check/action/list + worker job + scheduling (Task 4); live plugin verification (Task 5). The WebGUI button + firmware/plugins UI are explicitly deferred to the frontend plan (spec §6). No master switch (per decision).

**Placeholder scan:** every code step is complete; the two "confirm the exact fixture/attr names by reading the config-push tests/api" notes give a concrete file to copy from + a defined adaptation, not a vague TODO; the upgradestatus done-marker is concrete (`status != "running"`) with a live-confirm verification step.

**Type consistency:** `run_firmware_action(session, action, client, now)` (Task 3) is called by the worker job (Task 4); `FirmwareAction(kind, target, status, ...)` columns (Task 2) match the model used in Tasks 3-4; the connector methods (Task 1) match those the service/script call (Tasks 3, 5); `_updates_pending`/`_major_offered`/`_poll_until_done`/`_to_int` are defined once in `firmware_action.py` and imported by the API + the script; `FirmwareActionIn`/`Out`/`FirmwareCheckOut` consistent across the API + tests.
