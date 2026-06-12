# Apply-Pipeline Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-enqueue scheduled config/firmware actions dropped by a per-device lock-miss (a cron sweeper gated on the device advisory lock), and add an operator-triggered targeted **Revert** that undoes a config push by generating its inverse change through the existing apply pipeline.

**Architecture:** A `sweep_orphaned_actions` cron scans overdue `status='scheduled'` rows in `config_changes` + `firmware_actions`; per row it tries the device's `pg_try_advisory_xact_lock` — if held, a real op is running (skip); if free, the row is a genuine orphan (re-enqueue, or give up after `max_reenqueue_attempts` device-free tries). Revert builds the inverse `config_change` (`INVERSE_BUILDERS` registry; v1 `firewall_alias`) and runs it through `create_change` → schedule → `apply_config_change`.

**Tech Stack:** Python 3.14 · FastAPI · SQLAlchemy 2 async · Postgres advisory locks · arq · React 19 + Mantine v9 + openapi-fetch · pytest · vitest.

**Spec:** `docs/superpowers/specs/2026-06-12-apply-pipeline-reliability-design.md`
**Branch:** `feat/apply-pipeline-reliability` (already created).

---

## File Structure

**Backend — create:**
- `app/services/action_sweeper.py` — pure `decide_orphan(...)` give-up/re-enqueue policy.
- `app/services/config_revert.py` — `INVERSE_BUILDERS` registry, the `firewall_alias` inverse, snapshot→config.xml helper, and `revert_change`.
- `migrations/versions/0023_reliability.py` — `sweep_attempts` on both tables + `reverts_change_id` on config_changes.

**Backend — modify:**
- `app/core/config.py` — sweeper settings.
- `app/models/config_change.py` — `sweep_attempts`, `reverts_change_id`.
- `app/models/firmware_action.py` — `sweep_attempts`.
- `app/worker.py` — `sweep_orphaned_actions` cron + `WorkerSettings` registration.
- `app/api/config.py` — `POST …/changes/{id}/revert`.
- `app/schemas/config.py` — `ConfigChangeOut.reverts_change_id` + `revertible`.
- `app/repositories/config_change.py` — expose the data the `revertible` flag needs (kind + status are already on the row).

**Frontend — modify:**
- `frontend/src/api/schema.d.ts` (regenerated).
- the config-changes history component + its hooks (Revert button + call).

---

## Conventions (read once)

- Backend tests: from `backend/`, prefix DB tests with `TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test"`. They must RUN (pass), not skip.
- The advisory key helper already exists: `app.services.config_push._advisory_key(device_id) -> int`.
- Worker ctx provides `ctx["session_factory"]` and `ctx["redis"]` (with `.enqueue_job(name, *args)`).
- English everywhere; commit after each task's tests pass.

---

# PHASE A — Orphaned-action sweeper

## Task A1: Migration 0023 + model fields

**Files:**
- Create: `backend/migrations/versions/0023_reliability.py`
- Modify: `backend/app/models/config_change.py`, `backend/app/models/firmware_action.py`
- Test: `backend/tests/test_migration_0023.py`

- [ ] **Step 1: Confirm the head revision is 0022**

Run: `cd backend && ls migrations/versions/ | sort | tail -2`
Expected: `0022_report_delivery.py` is the latest. If not, STOP and report.

- [ ] **Step 2: Write the migration test**

```python
# backend/tests/test_migration_0023.py
from sqlalchemy import text


async def test_migration_0023_columns(db_engine):
    async with db_engine.begin() as conn:
        cc = (await conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='config_changes'"
        ))).scalars().all()
        assert "sweep_attempts" in cc
        assert "reverts_change_id" in cc
        fa = (await conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='firmware_actions'"
        ))).scalars().all()
        assert "sweep_attempts" in fa
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd backend && TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" .venv/bin/pytest tests/test_migration_0023.py -v`
Expected: FAIL (columns missing — the metadata schema has no such columns yet).

- [ ] **Step 4: Add the model fields**

`backend/app/models/config_change.py` — add (after `status`):
```python
    sweep_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    reverts_change_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("config_changes.id", ondelete="SET NULL"), nullable=True
    )
```
(Confirm `Integer` and `ForeignKey` are imported in that file; add to the existing import line if missing.)

`backend/app/models/firmware_action.py` — add (after `status`):
```python
    sweep_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
```
(Add `Integer` to its `from sqlalchemy import (...)` line if missing.)

- [ ] **Step 5: Write the migration**

```python
# backend/migrations/versions/0023_reliability.py
"""sweeper attempts (config_changes + firmware_actions) + config_changes.reverts_change_id"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("config_changes",
                  sa.Column("sweep_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("config_changes",
                  sa.Column("reverts_change_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_config_changes_reverts", "config_changes", "config_changes",
                          ["reverts_change_id"], ["id"], ondelete="SET NULL")
    op.add_column("firmware_actions",
                  sa.Column("sweep_attempts", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("firmware_actions", "sweep_attempts")
    op.drop_constraint("fk_config_changes_reverts", "config_changes", type_="foreignkey")
    op.drop_column("config_changes", "reverts_change_id")
    op.drop_column("config_changes", "sweep_attempts")
```

- [ ] **Step 6: Verify on a scratch DB**

```bash
cd backend
.venv/bin/python - <<'PY'
import asyncio, asyncpg
async def m():
    c = await asyncpg.connect(host="localhost", user="opngms", password="opngms", database="postgres")
    await c.execute("DROP DATABASE IF EXISTS opngms_mig0023"); await c.execute("CREATE DATABASE opngms_mig0023"); await c.close()
asyncio.run(m())
PY
ALEMBIC_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_mig0023" .venv/bin/alembic upgrade head
ALEMBIC_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_mig0023" .venv/bin/alembic downgrade -1
ALEMBIC_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_mig0023" .venv/bin/alembic upgrade head
.venv/bin/python - <<'PY'
import asyncio, asyncpg
async def m():
    c = await asyncpg.connect(host="localhost", user="opngms", password="opngms", database="postgres")
    await c.execute("DROP DATABASE IF EXISTS opngms_mig0023"); await c.close()
asyncio.run(m())
PY
```
Expected: `Running upgrade 0022 -> 0023`, clean downgrade + re-upgrade. Then run the metadata test:
`cd backend && TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" .venv/bin/pytest tests/test_migration_0023.py -v` → PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/migrations/versions/0023_reliability.py backend/app/models/config_change.py backend/app/models/firmware_action.py backend/tests/test_migration_0023.py
git commit -m "feat(reliability): migration 0023 — sweep_attempts + reverts_change_id"
```

---

## Task A2: Sweeper settings

**Files:**
- Modify: `backend/app/core/config.py`

- [ ] **Step 1: Add settings**

In `Settings` (`backend/app/core/config.py`), add near the other worker-cron cadences:
```python
    sweep_every_minutes: int = 5          # orphaned-action sweeper cadence (1..30)
    orphan_grace_minutes: int = 5         # don't touch a scheduled row until this overdue
    max_reenqueue_attempts: int = 5       # give up an orphan after this many device-free re-enqueues
```

- [ ] **Step 2: Verify import**

Run: `cd backend && .venv/bin/python -c "from app.core.config import Settings; print(Settings.model_fields['max_reenqueue_attempts'].default)"`
Expected: `5`.

- [ ] **Step 3: Commit**

```bash
git add backend/app/core/config.py
git commit -m "feat(reliability): sweeper settings"
```

---

## Task A3: `decide_orphan` policy (pure)

**Files:**
- Create: `backend/app/services/action_sweeper.py`
- Test: `backend/tests/test_action_sweeper.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_action_sweeper.py
from app.services.action_sweeper import decide_orphan


def test_reenqueue_while_attempts_remain():
    assert decide_orphan(sweep_attempts=0, max_attempts=5) == "re-enqueue"
    assert decide_orphan(sweep_attempts=4, max_attempts=5) == "re-enqueue"


def test_give_up_at_or_past_max():
    assert decide_orphan(sweep_attempts=5, max_attempts=5) == "give-up"
    assert decide_orphan(sweep_attempts=9, max_attempts=5) == "give-up"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_action_sweeper.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

```python
# backend/app/services/action_sweeper.py
"""Pure give-up policy for the orphaned-action sweeper (no DB/lock — unit-testable)."""


def decide_orphan(*, sweep_attempts: int, max_attempts: int) -> str:
    """Given how many device-free re-enqueues a scheduled orphan has already had, decide the action.

    Returns 're-enqueue' while attempts remain, else 'give-up'. The caller only invokes this once it
    has confirmed (via the device advisory lock) that the device is free — so attempts count only
    genuine device-free retries.
    """
    return "re-enqueue" if sweep_attempts < max_attempts else "give-up"
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && .venv/bin/pytest tests/test_action_sweeper.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/action_sweeper.py backend/tests/test_action_sweeper.py
git commit -m "feat(reliability): decide_orphan give-up policy"
```

---

## Task A4: `sweep_orphaned_actions` cron

**Files:**
- Modify: `backend/app/worker.py`
- Test: `backend/tests/test_worker_sweeper.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_worker_sweeper.py
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.worker as worker
from app.core.db import set_tenant_context
from app.models.config_change import ConfigChange
from app.services.config_push import _advisory_key


class FakeRedis:
    def __init__(self):
        self.calls = []

    async def enqueue_job(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))


async def _seed_change(factory, *, status="scheduled", scheduled_at, sweep_attempts=0):
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await set_tenant_context(s, tid)
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        c = ConfigChange(tenant_id=tid, device_id=did, created_by=uuid.uuid4(), kind="alias",
                         operation="add", target="A", payload={}, baseline_hash="", status=status,
                         scheduled_at=scheduled_at, sweep_attempts=sweep_attempts)
        s.add(c)
        await s.commit()
        return tid, did, c.id


async def test_overdue_orphan_is_reenqueued(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    past = datetime.now(UTC) - timedelta(hours=1)
    _, _, cid = await _seed_change(factory, scheduled_at=past)
    redis = FakeRedis()
    summary = await worker.sweep_orphaned_actions({"session_factory": factory, "redis": redis})
    assert redis.calls and redis.calls[0][0] == "apply_config_change"
    assert redis.calls[0][1][0] == str(cid)
    async with factory() as s:
        assert (await s.get(ConfigChange, cid)).sweep_attempts == 1
    assert summary["re_enqueued"] >= 1


async def test_recent_scheduled_within_grace_is_untouched(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    soon = datetime.now(UTC) - timedelta(seconds=30)  # within the 5-min grace
    await _seed_change(factory, scheduled_at=soon)
    redis = FakeRedis()
    await worker.sweep_orphaned_actions({"session_factory": factory, "redis": redis})
    assert redis.calls == []


async def test_device_busy_is_skipped(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    past = datetime.now(UTC) - timedelta(hours=1)
    _, did, cid = await _seed_change(factory, scheduled_at=past)
    # Hold the device advisory lock on a SEPARATE connection for the duration of the sweep.
    holder = async_sessionmaker(db_engine, expire_on_commit=False)
    async with holder() as hs:
        await hs.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _advisory_key(did)})
        redis = FakeRedis()
        summary = await worker.sweep_orphaned_actions({"session_factory": factory, "redis": redis})
        await hs.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _advisory_key(did)})
    assert redis.calls == []                  # skipped: device busy
    assert summary["skipped"] >= 1
    async with factory() as s:
        assert (await s.get(ConfigChange, cid)).sweep_attempts == 0  # attempts NOT burned


async def test_attempts_exhausted_gives_up(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    past = datetime.now(UTC) - timedelta(hours=1)
    tid, did, cid = await _seed_change(factory, scheduled_at=past, sweep_attempts=5)  # == max
    redis = FakeRedis()
    await worker.sweep_orphaned_actions({"session_factory": factory, "redis": redis})
    assert redis.calls == []
    async with factory() as s:
        c = await s.get(ConfigChange, cid)
        assert c.status == "failed"
        assert "orphaned" in c.result.get("error", "")
        n = (await s.execute(text("SELECT count(*) FROM alerts WHERE device_id=:d"), {"d": did})).scalar_one()
        assert n >= 1
```

> Note: `pg_advisory_lock` (session-level) is used in the test holder so the lock persists across the
> sweep; the sweeper itself uses `pg_try_advisory_xact_lock` (transaction-level), which contends with
> it on the same key.

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" .venv/bin/pytest tests/test_worker_sweeper.py -v`
Expected: FAIL (`sweep_orphaned_actions` doesn't exist).

- [ ] **Step 3: Implement the cron in `backend/app/worker.py`**

Add imports near the top (worker.py already imports `select`, `timedelta`, `datetime`, `UTC`,
`get_settings`; add `func` to the SQLAlchemy import and the two services):
```python
from sqlalchemy import func, select   # extend the existing `from sqlalchemy import select`
from app.services.action_sweeper import decide_orphan
from app.services.config_push import _advisory_key
```
Add the function (place it near the other cron functions):
```python
async def sweep_orphaned_actions(ctx: dict) -> dict:
    """Cron: re-enqueue scheduled config/firmware actions dropped by a device lock-miss.

    For each overdue scheduled row, try the device advisory lock: if a real op holds it, skip; if
    free, the row is a genuine orphan — re-enqueue (counting device-free attempts) or give up.
    Runs as owner (RLS-exempt); each row in its own transaction so one bad row can't abort the sweep.
    """
    from app.models.alert import Alert as AlertModel
    from app.models.config_change import ConfigChange
    from app.models.firmware_action import FirmwareAction

    settings = get_settings()
    grace = timedelta(minutes=settings.orphan_grace_minutes)
    max_attempts = settings.max_reenqueue_attempts
    now = datetime.now(UTC)
    cutoff = now - grace
    factory = ctx["session_factory"]
    redis = ctx["redis"]
    summary = {"re_enqueued": 0, "gave_up": 0, "skipped": 0}

    specs = [
        (ConfigChange, "apply_config_change"),
        (FirmwareAction, "run_firmware_action"),
    ]
    for model, job_name in specs:
        async with factory() as session:
            ids = (await session.execute(
                select(model.id).where(
                    model.status == "scheduled",
                    func.coalesce(model.scheduled_at, model.created_at) < cutoff,
                )
            )).scalars().all()
        for row_id in ids:
            try:
                async with factory() as session:
                    row = await session.get(model, row_id)
                    if row is None or row.status != "scheduled":
                        continue
                    got = (await session.execute(
                        text("SELECT pg_try_advisory_xact_lock(:k)"),
                        {"k": _advisory_key(row.device_id)},
                    )).scalar_one()
                    if not got:
                        summary["skipped"] += 1
                        await session.rollback()
                        continue
                    if decide_orphan(sweep_attempts=row.sweep_attempts, max_attempts=max_attempts) == "re-enqueue":
                        row.sweep_attempts += 1
                        await session.commit()  # releases the xact lock before the job re-acquires it
                        await redis.enqueue_job(job_name, str(row_id))
                        summary["re_enqueued"] += 1
                    else:
                        row.status = "failed"
                        row.result = {"error": f"orphaned: never applied after {row.sweep_attempts} re-enqueue attempts"}
                        session.add(AlertModel(tenant_id=row.tenant_id, device_id=row.device_id,
                                               type="action_orphaned",
                                               label=f"{job_name} {row_id} given up after {row.sweep_attempts} attempts"))
                        await session.commit()
                        summary["gave_up"] += 1
            except Exception:  # noqa: BLE001 — one bad row must not abort the sweep
                continue
    return summary
```
> Verify the `Alert` model constructor fields by reading `app/models/alert.py` first (the spec notes
> `Alert(tenant_id, device_id, type, label)`); adjust the kwargs to match exactly. Remove the stray
> `from app.services.alerting import Alert` line — use only `from app.models.alert import Alert as AlertModel`.

- [ ] **Step 4: Register the cron in `WorkerSettings`**

In `backend/app/worker.py`, add to `functions` the new job is a cron (not a job function); add to `cron_jobs`:
```python
        cron(sweep_orphaned_actions, minute=set(range(0, 60, min(30, max(1, _settings.sweep_every_minutes))))),
```
(Place it alongside the other `cron(...)` entries.)

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" .venv/bin/pytest tests/test_worker_sweeper.py -v`
Expected: PASS (4 tests). Also `.venv/bin/ruff check app/worker.py app/services/action_sweeper.py` clean.

- [ ] **Step 6: Commit**

```bash
git add backend/app/worker.py backend/tests/test_worker_sweeper.py
git commit -m "feat(reliability): sweep_orphaned_actions cron (advisory-lock-gated re-enqueue)"
```

---

# PHASE B — Operator-triggered Revert (config-push)

## Task B1: Inverse builders + snapshot helper

**Files:**
- Create: `backend/app/services/config_revert.py`
- Test: `backend/tests/test_config_revert.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_config_revert.py
import uuid

import pytest

from app.models.config_change import ConfigChange
from app.services.config_revert import (
    NoInverseError,
    build_inverse,
    has_inverse,
    alias_from_config_xml,
)


def _change(operation, target, payload):
    return ConfigChange(tenant_id=uuid.uuid4(), device_id=uuid.uuid4(), created_by=uuid.uuid4(),
                        kind="alias", operation=operation, target=target, payload=payload,
                        baseline_hash="", status="applied")


def test_has_inverse():
    assert has_inverse("alias") is True
    assert has_inverse("opnsense_setting") is False


def test_add_inverts_to_delete_without_snapshot():
    op, target, payload = build_inverse(_change("add", "WebServers", {"name": "WebServers", "type": "host"}), None)
    assert op == "delete"
    assert target == "WebServers"
    assert payload == {"name": "WebServers"}


def test_delete_inverts_to_add_from_snapshot():
    xml = (
        "<opnsense><OPNsense><Firewall><Alias><aliases>"
        "<alias uuid='u1'><enabled>1</enabled><name>WebServers</name><type>host</type>"
        "<content>10.0.0.1\n10.0.0.2</content><description>web</description></alias>"
        "</aliases></Alias></Firewall></OPNsense></opnsense>"
    )
    op, target, payload = build_inverse(_change("delete", "WebServers", {"name": "WebServers"}), xml)
    assert op == "add"
    assert target == "WebServers"
    assert payload["name"] == "WebServers"
    assert payload["type"] == "host"
    assert payload["content"] == "10.0.0.1\n10.0.0.2"


def test_set_inverts_to_set_previous_from_snapshot():
    xml = (
        "<opnsense><OPNsense><Firewall><Alias><aliases>"
        "<alias uuid='u1'><name>WebServers</name><type>host</type><content>1.1.1.1</content></alias>"
        "</aliases></Alias></Firewall></OPNsense></opnsense>"
    )
    op, target, payload = build_inverse(_change("set", "WebServers", {"name": "WebServers", "content": "2.2.2.2"}), xml)
    assert op == "set"
    assert payload["content"] == "1.1.1.1"


def test_delete_without_snapshot_raises():
    with pytest.raises(NoInverseError):
        build_inverse(_change("delete", "WebServers", {"name": "WebServers"}), None)


def test_unknown_kind_raises():
    c = _change("add", "x", {})
    c.kind = "opnsense_setting"
    with pytest.raises(NoInverseError):
        build_inverse(c, None)


def test_alias_from_config_xml_missing_returns_none():
    xml = "<opnsense><OPNsense><Firewall><Alias><aliases></aliases></Alias></Firewall></OPNsense></opnsense>"
    assert alias_from_config_xml(xml, "nope") is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_config_revert.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

```python
# backend/app/services/config_revert.py
"""Operator-triggered revert: build the inverse of a config_change and a snapshot reader.

The inverse is a normal config_change run through the existing apply pipeline; only the
inverse-generation is new. v1 registers the firewall_alias kind; other kinds raise NoInverseError
(the Revert button is disabled for them) until their builders are added.
"""
from __future__ import annotations

import gzip
from collections.abc import Callable

from defusedxml import ElementTree as DET

from app.core import crypto
from app.models.config_change import ConfigChange

# (operation, target, payload) for the inverse change.
InverseBuilder = Callable[[ConfigChange, str | None], tuple[str, str, dict]]


class NoInverseError(Exception):
    """No inverse can be built (unknown kind, or a delete/set with no pre-apply snapshot)."""


INVERSE_BUILDERS: dict[str, InverseBuilder] = {}


def register_inverse_builder(kind: str, fn: InverseBuilder) -> None:
    INVERSE_BUILDERS[kind] = fn


def has_inverse(kind: str) -> bool:
    return kind in INVERSE_BUILDERS


def build_inverse(change: ConfigChange, snapshot_xml: str | None) -> tuple[str, str, dict]:
    fn = INVERSE_BUILDERS.get(change.kind)
    if fn is None:
        raise NoInverseError(f"no inverse builder for kind {change.kind!r}")
    return fn(change, snapshot_xml)


def snapshot_to_xml(content_enc: bytes) -> str:
    """Decrypt + gunzip a config_snapshot.content_enc back into the config.xml string."""
    return gzip.decompress(crypto.decrypt_bytes(bytes(content_enc))).decode("utf-8")


def alias_from_config_xml(xml: str, name: str) -> dict | None:
    """Extract the <alias> with the given <name> from a config.xml as a flat {tag: text} payload."""
    root = DET.fromstring(xml)
    for alias in root.iter("alias"):
        name_el = alias.find("name")
        if name_el is not None and (name_el.text or "") == name:
            return {child.tag: (child.text or "") for child in alias}
    return None


def _invert_alias(change: ConfigChange, snapshot_xml: str | None) -> tuple[str, str, dict]:
    name = change.target or change.payload.get("name", "")
    if change.operation == "add":
        return "delete", name, {"name": name}
    # delete / set both need the pre-apply alias definition from the snapshot.
    if not snapshot_xml:
        raise NoInverseError("no pre-apply snapshot to reconstruct the alias from")
    prev = alias_from_config_xml(snapshot_xml, name)
    if prev is None:
        raise NoInverseError(f"alias {name!r} not found in the pre-apply snapshot")
    prev.setdefault("name", name)
    inverse_op = "add" if change.operation == "delete" else "set"
    return inverse_op, name, prev


register_inverse_builder("alias", _invert_alias)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && .venv/bin/pytest tests/test_config_revert.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/config_revert.py backend/tests/test_config_revert.py
git commit -m "feat(reliability): inverse builders + snapshot reader (alias revert)"
```

---

## Task B2: `revert_change` service

**Files:**
- Modify: `backend/app/services/config_revert.py`
- Test: `backend/tests/test_config_revert_service.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_config_revert_service.py
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.models.config_change import ConfigChange
from app.services.config_revert import RevertError, revert_change


async def _seed(factory):
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await set_tenant_context(s, tid)
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        change = ConfigChange(tenant_id=tid, device_id=did, created_by=uuid.uuid4(), kind="alias",
                              operation="add", target="A", payload={"name": "A", "type": "host"},
                              baseline_hash="", status="applied",
                              applied_at=datetime(2026, 6, 1, tzinfo=timezone.utc))
        s.add(change)
        await s.commit()
        return tid, did, change.id


async def test_revert_creates_linked_inverse(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did, cid = await _seed(factory)
    async with factory() as s:
        await set_tenant_context(s, tid)
        change = await s.get(ConfigChange, cid)
        inverse = await revert_change(s, change, actor_id=uuid.uuid4())
        await s.commit()
        assert inverse.operation == "delete"
        assert inverse.reverts_change_id == cid
        assert inverse.kind == "alias"
        assert inverse.status == "draft"


async def test_revert_rejects_non_revertible_state(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did, cid = await _seed(factory)
    async with factory() as s:
        await set_tenant_context(s, tid)
        change = await s.get(ConfigChange, cid)
        change.status = "scheduled"
        with __import__("pytest").raises(RevertError):
            await revert_change(s, change, actor_id=uuid.uuid4())
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" .venv/bin/pytest tests/test_config_revert_service.py -v`
Expected: FAIL (`revert_change` / `RevertError` missing).

- [ ] **Step 3: Implement (append to `backend/app/services/config_revert.py`)**

```python
# --- revert flow ---
import uuid  # noqa: E402  (kept local to the appended section)

from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.repositories.config_snapshot import ConfigSnapshotRepository  # noqa: E402
from app.services.config_push import create_change  # noqa: E402


class RevertError(Exception):
    """The change cannot be reverted (wrong state, or no inverse for its kind)."""


REVERTIBLE_STATES = ("applied", "failed")


async def revert_change(session: AsyncSession, change: ConfigChange, *, actor_id: uuid.UUID) -> ConfigChange:
    """Build the inverse of `change` as a new draft config_change linked via reverts_change_id.

    The caller schedules/applies the returned draft through the normal pipeline.
    """
    if change.status not in REVERTIBLE_STATES:
        raise RevertError(f"cannot revert a change in status {change.status}")
    if not has_inverse(change.kind):
        raise RevertError(f"revert not supported for kind {change.kind!r}")
    snapshot_xml: str | None = None
    if change.pre_apply_snapshot_id is not None:
        snap = await ConfigSnapshotRepository(session, change.tenant_id).get(change.pre_apply_snapshot_id)
        if snap is not None:
            snapshot_xml = snapshot_to_xml(snap.content_enc)
    op, target, payload = build_inverse(change, snapshot_xml)  # may raise NoInverseError
    inverse = await create_change(
        session, tenant_id=change.tenant_id, device_id=change.device_id, created_by=actor_id,
        kind=change.kind, operation=op, target=target, payload=payload,
    )
    inverse.reverts_change_id = change.id
    await session.flush()
    return inverse
```
> Confirm `ConfigSnapshotRepository` has a `get(snapshot_id)` method; if it is named differently (e.g.
> `by_id`), use that. `build_inverse` raising `NoInverseError` propagates — the API maps it to 409.

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" .venv/bin/pytest tests/test_config_revert_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/config_revert.py backend/tests/test_config_revert_service.py
git commit -m "feat(reliability): revert_change service (linked inverse draft)"
```

---

## Task B3: Revert API + ConfigChangeOut fields

**Files:**
- Modify: `backend/app/schemas/config.py`, `backend/app/api/config.py`
- Test: `backend/tests/test_config_revert_api.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_config_revert_api.py
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.models.config_change import ConfigChange
from tests.conftest import csrf_headers
from tests.factories import make_membership, make_user


async def _seed(db_engine, *, status="applied", kind="alias", operation="add"):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        admin = await make_user(s, email="admin@x.io", password="pw12345")
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await make_membership(s, user_id=admin.id, tenant_id=tid, role="tenant_admin")
        await set_tenant_context(s, tid)
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        c = ConfigChange(tenant_id=tid, device_id=did, created_by=admin.id, kind=kind,
                         operation=operation, target="A", payload={"name": "A", "type": "host"},
                         baseline_hash="", status=status)
        s.add(c)
        await s.commit()
        return tid, did, c.id


async def _login(api_client, email="admin@x.io"):
    r = await api_client.post("/api/login", json={"email": email, "password": "pw12345"})
    assert r.status_code == 200, r.text


async def test_revert_creates_and_schedules_inverse(api_client, db_engine):
    from app.core.queue import get_enqueuer
    from app.main import app

    calls = []
    async def fake_enqueue(name, *a, **k): calls.append((name, a, k))
    app.dependency_overrides[get_enqueuer] = lambda: fake_enqueue
    try:
        tid, did, cid = await _seed(db_engine)
        await _login(api_client)
        r = await api_client.post(
            f"/api/tenants/{tid}/devices/{did}/config/changes/{cid}/revert",
            headers=csrf_headers(api_client), json={})
        assert r.status_code in (200, 201), r.text
        body = r.json()
        assert body["operation"] == "delete"
        assert calls and calls[0][0] == "apply_config_change"
    finally:
        app.dependency_overrides.pop(get_enqueuer, None)


async def test_revert_rejects_non_invertible_kind(api_client, db_engine):
    tid, did, cid = await _seed(db_engine, kind="opnsense_setting")
    await _login(api_client)
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/config/changes/{cid}/revert",
        headers=csrf_headers(api_client), json={})
    assert r.status_code == 409


async def test_list_exposes_reverts_and_revertible(api_client, db_engine):
    tid, did, cid = await _seed(db_engine)
    await _login(api_client)
    g = await api_client.get(f"/api/tenants/{tid}/devices/{did}/config/changes")
    assert g.status_code == 200
    row = next(r for r in g.json() if r["id"] == str(cid))
    assert row["revertible"] is True
    assert row["reverts_change_id"] is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" .venv/bin/pytest tests/test_config_revert_api.py -v`
Expected: FAIL (endpoint + fields missing).

- [ ] **Step 3: Extend `ConfigChangeOut` (`backend/app/schemas/config.py`)**

Add to `ConfigChangeOut`:
```python
    reverts_change_id: uuid.UUID | None = None
    revertible: bool = False
```
Add a builder so the API can compute `revertible` (it is not a column). In `app/api/config.py`, add a helper and use it in `list_config_changes` (and the revert/return paths) instead of returning the ORM object directly:
```python
from app.services.config_revert import has_inverse

def _change_out(change) -> ConfigChangeOut:
    return ConfigChangeOut(
        id=change.id, device_id=change.device_id, kind=change.kind, operation=change.operation,
        target=change.target, status=change.status, scheduled_at=change.scheduled_at,
        applied_at=change.applied_at, created_at=change.created_at,
        reverts_change_id=change.reverts_change_id,
        revertible=(change.status in ("applied", "failed") and has_inverse(change.kind)),
    )
```
Change `list_config_changes` to `return [_change_out(c) for c in await ConfigChangeRepository(session, tenant_id).list(device_id)]` and its return type to `list[ConfigChangeOut]`.

- [ ] **Step 4: Add the revert endpoint (`backend/app/api/config.py`)**

```python
from app.schemas.config import ScheduleIn  # if not already imported
from app.services.config_revert import NoInverseError, RevertError, revert_change


@router.post(
    "/devices/{device_id}/config/changes/{change_id}/revert",
    response_model=ConfigChangeOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_csrf)],
)
async def revert_config_change(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    change_id: uuid.UUID,
    body: ScheduleIn,
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.CONFIG_PUSH)),
    session: AsyncSession = Depends(get_session),
    enqueue=Depends(get_enqueuer),
) -> ConfigChangeOut:
    repo = ConfigChangeRepository(session, tenant_id)
    change = await repo.get(change_id)
    if change is None or change.device_id != device_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Change not found")
    try:
        inverse = await revert_change(session, change, actor_id=ctx.user.id)
    except (RevertError, NoInverseError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    inverse.status = "scheduled"
    inverse.scheduled_at = body.scheduled_at
    await session.flush()
    await AuditService(session).record(
        actor_user_id=ctx.user.id, tenant_id=tenant_id, action="config.change.revert",
        target_type="config_change", target_id=str(inverse.id),
        ip=request.client.host if request.client else None,
        details={"reverts": str(change_id), "operation": inverse.operation},
    )
    out = _change_out(inverse)
    await session.commit()
    await enqueue("apply_config_change", str(inverse.id), defer_until=body.scheduled_at)
    return out
```

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" .venv/bin/pytest tests/test_config_revert_api.py tests/test_config_api.py tests/test_config_rls_api.py -v`
Expected: PASS (new tests + existing config-API tests still green — the `_change_out` switch must not break them).

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas/config.py backend/app/api/config.py backend/tests/test_config_revert_api.py
git commit -m "feat(reliability): revert API + revertible/reverts_change_id in ConfigChangeOut"
```

---

## Task B4: Regenerate OpenAPI + frontend Revert button

**Files:**
- Modify: `frontend/src/api/schema.d.ts` (generated), the config-changes history component + hooks.
- Test: the existing config-changes component test (extend) or a new one.

- [ ] **Step 1: Regenerate the client**

Run: `cd frontend && npm run gen:api`
Then: `grep -c "revert" src/api/schema.d.ts && grep -c "revertible" src/api/schema.d.ts` → both > 0.

- [ ] **Step 2: Locate the config-changes UI**

Run: `cd frontend && grep -rln "config/changes" src/` to find the component + hook that lists changes (likely under `src/config/`). Read them.

- [ ] **Step 3: Write the failing test**

In the config-changes component's test file (mirror the existing tenant-scoped test pattern with a local `withTenant` + MSW), add a test: mock `GET …/config/changes` returning one row with `revertible: true, status: "applied"`, render, assert a `data-testid="revert-<id>"` button exists; mock `POST …/changes/<id>/revert` capturing the call; click the button (+ confirm if there is a modal); assert the POST fired. Run `npm test -- <file>` → FAIL (no button yet).

- [ ] **Step 4: Implement**

- Add a `useRevertChange()` mutation in the config-changes hooks file mirroring the existing schedule/apply mutation:
```ts
export function useRevertChange(deviceId: string) {
  const { activeId } = useTenant();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (changeId: string) => {
      const { data, error } = await api.POST(
        "/api/tenants/{tenant_id}/devices/{device_id}/config/changes/{change_id}/revert",
        { params: { path: { tenant_id: activeId!, device_id: deviceId, change_id: changeId } }, body: {} });
      if (error || !data) throw new Error("Failed to revert change");
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["config-changes", activeId, deviceId] }),
  });
}
```
(Adjust the query key to whatever the existing list query uses.)
- In the history row, when `row.revertible`, render a `Button size="xs" variant="light"` `data-testid={`revert-${row.id}`}` that calls `revert.mutate(row.id)` (reuse any existing confirm-modal pattern). When `row.reverts_change_id`, show a small "reverts #…" `Text`/`Badge`.

- [ ] **Step 5: Verify + build gate**

Run: `cd frontend && npm test -- <file> && npm run build`
Both MUST pass (`tsc -b` + vite).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/schema.d.ts frontend/openapi.json frontend/src/config/
git commit -m "feat(reliability): Revert button in the config-changes history"
```

---

## Final verification

- [ ] **Backend full suite:** `cd backend && TEST_DATABASE_URL=… .venv/bin/pytest -q` → all pass; `ruff check app` clean.
- [ ] **Frontend:** `cd frontend && npm run build && npm test` → all pass.
- [ ] **Grep:** `grep -rn "sweep_orphaned_actions\|revert_change\|INVERSE_BUILDERS" backend/app` → wired in worker + API.
- [ ] **Security review:** dispatch `security-reviewer` over the diff (advisory-lock correctness, the revert's CONFIG_PUSH+CSRF gating + tenant/device scoping + LIVE_PUSH_ENABLED, snapshot decryption, defusedxml parsing). Address BLOCKER/IMPORTANT.
- [ ] **Finish:** `superpowers:finishing-a-development-branch` → PR with green CI, merge per protected-main.

---

## Self-review notes (author)

- **Spec coverage:** sweeper cron + advisory-lock gate + attempt give-up (A3/A4) ✓; `sweep_attempts` + `reverts_change_id` migration (A1) ✓; settings (A2) ✓; inverse registry + alias add/delete/set + snapshot reader (B1) ✓; `revert_change` linked inverse (B2) ✓; revert API + `revertible`/`reverts_change_id` (B3) ✓; frontend button + reverts link (B4) ✓; no committed-stuck handling (correctly absent per the transaction model) ✓; out-of-scope (other kinds / firmware revert / full restore) recorded in memory, button disabled for non-invertible kinds ✓.
- **Type consistency:** `decide_orphan(*, sweep_attempts, max_attempts) -> str` identical A3/A4; `build_inverse(change, xml|None) -> (op, target, payload)` identical B1/B2; `revert_change(session, change, *, actor_id) -> ConfigChange` B2/B3; `_change_out` used by both list + revert in B3.
- **Phasing:** Phase A (sweeper) ships independently; Phase B (revert) depends only on A1's columns.
