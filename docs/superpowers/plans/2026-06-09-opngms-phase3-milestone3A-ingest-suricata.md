# OPNGMS — Phase 3 / Milestone 3A: Storage + Ingest Framework + Suricata — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Incremental and idempotent ingestion of Suricata IDS/IPS alerts from the OPNsense fleet into a tenant-isolated `events` hypertable, with a per-device cursor and deduplication.

**Architecture:** Extends the ARQ worker with a second cron (`enqueue_event_ingests`, ~5 min) that enqueues `ingest_device_events(device_id)`. The job reads a cursor for `(device, source)`, queries the OPNsense API via `OpnsenseClient` (SSRF-guarded), normalizes IDS alerts, and inserts them into `events` with `ON CONFLICT DO NOTHING` (dedup), advancing the cursor. DB owner (bypasses RLS) for writes; RLS will protect `events` for API reads (3C).

**Tech Stack:** Python 3.12+, FastAPI/SQLAlchemy 2.0 async, TimescaleDB (hypertable `events`), ARQ + Redis, Alembic, pytest + respx.

---

## Context for the implementer (read before starting)

Backend codebase at `/home/l0rdg3x/coding/OPNGMS/backend`. **Follow Phase 2 patterns.**

- **Hypertable model**: `app/models/metric.py` — `Metric` with a composite PK that INCLUDES `time` (required by Timescale), `__table_args__` with an `Index`. Replicate for `events`.
- **Hypertable migration**: `migrations/versions/0005_timescale_metrics.py` — `create_table` + `create_hypertable('metrics','time')` + index + `add_retention_policy`. Replicate for `events`.
- **RLS migration**: `migrations/versions/0007_rls_metrics_alerts.py` — enable/force/policy + grant to `opngms_app` (with explicit `GRANT SELECT ON <hypertable>` for propagation to Timescale chunks). Replicate for `events`.
- **RLS — single source of truth**: `app/core/rls.py` — `TENANT_TABLES` (currently `["devices","metrics","alerts"]`). Historical migrations 0002/0003 are PINNED to `["devices"]` and 0007 to `["metrics","alerts"]`: adding `"events"` to `TENANT_TABLES` breaks nothing, and the test conftest enables RLS on all `TENANT_TABLES`.
- **conftest**: `tests/conftest.py` — the `db_engine` fixture creates the extension, runs `create_all`, `create_hypertable('metrics', ...)`, enables RLS (`enable_rls_statements()`), creates the `opngms_app` role + grants. **`create_hypertable('events', ...)` must be added** (Task 1). Useful fixtures: `two_tenants` (two tenants + one device each: `fw-a`/`fw-b`).
- **Worker**: `app/worker.py` — `enqueue_device_polls` (cron) + `poll_device` (job) + `WorkerSettings`. Replicate the pattern for events.
- **Collection service**: `app/services/monitoring.py` — `collect_and_store(session, device, client, now)`: resilient try/except `OpnsenseError`, builds ORM rows, `session.add_all`, `flush`. Replicate the spirit for `ingest`.
- **Connector**: `app/connectors/opnsense/client.py` — `OpnsenseClient`, private method `_get(path)` (single HTTP boundary, SSRF-guarded, error normalisation → `OpnsenseError` and subclasses). Public methods (`get_interfaces`, etc.) return normalised dicts. Replicate for `get_ids_alerts`.
- **Connector tests**: `tests/test_connector_network.py` / `test_connector_system_info.py` — use `respx` to mock HTTP responses.

**Test command** (from `backend/`):
```
TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" \
ADMIN_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" \
.venv/bin/python -m pytest -q
```
Test DB in Docker (`docker compose ps` → `db`). Current suite: **127 green tests**.

**`alembic check` on a clean DB** (procedure used in Phase 2): create DB `opngms_check` + timescaledb extension, `alembic upgrade head`, `alembic check` (expected "No new upgrade operations detected"), drop. The `SESSION_SECRET`/`MASTER_KEY` env vars are required (see plans 2C/2D).

⚠️ **OPNsense IDS endpoint TO BE VERIFIED**: the actual endpoint (presumably `ids/service/queryAlerts`) and the payload format are not confirmed. The `get_ids_alerts` connector is written against a *plausible* payload and tested with respx; the mapping should be confirmed on a real device. **NOT** a blocker: the abstraction and tests hold regardless.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `app/models/event.py` | `Event` (hypertable) | Create |
| `app/models/ingest_cursor.py` | `IngestCursor` (worker state) | Create |
| `app/models/__init__.py` | Export the new models | Modify |
| `app/core/rls.py` | `"events"` in `TENANT_TABLES` | Modify |
| `migrations/versions/0008_events_ingest.py` | events hypertable + ingest_cursors + RLS + grant | Create |
| `tests/conftest.py` | `create_hypertable('events', ...)` | Modify |
| `app/connectors/opnsense/client.py` | `get_ids_alerts(since)` | Modify |
| `app/services/ingest.py` | `ingest_events(...)` (cursor, dedup, IDS) | Create |
| `app/worker.py` | cron `enqueue_event_ingests` + `ingest_device_events` | Modify |
| `tests/test_event_model.py`, `tests/test_rls_isolation.py` | model + RLS isolation for events | Create/Modify |
| `tests/test_connector_ids.py` | respx for `get_ids_alerts` | Create |
| `tests/test_ingest.py` | write/cursor/idempotency/resilience | Create |
| `tests/test_worker_config.py` | cron/job wiring | Modify |

---

## Task 1: `events` + `ingest_cursors` models, migration 0008, RLS

**Files:**
- Create: `app/models/event.py`, `app/models/ingest_cursor.py`
- Modify: `app/models/__init__.py`, `app/core/rls.py`, `tests/conftest.py`
- Create: `migrations/versions/0008_events_ingest.py`
- Create: `tests/test_event_model.py`; Modify: `tests/test_rls_isolation.py`

- [ ] **Step 1: Write the `Event` model**

Create `app/models/event.py` (mirror of `metric.py`; composite PK = dedup key, includes `time`):
```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index(
            "ix_events_tenant_device_source_time",
            "tenant_id", "device_id", "source", "time",
        ),
    )

    # Composite PK that includes `time` (required by Timescale) and is also the
    # dedup key: same (time, device, source, event_key) -> same event.
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source: Mapped[str] = mapped_column(String, primary_key=True)         # 'ids' | 'dns'
    event_key: Mapped[str] = mapped_column(String, primary_key=True)      # source id or content hash
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    category: Mapped[str] = mapped_column(String, default="", server_default="")
    src_ip: Mapped[str] = mapped_column(String, default="", server_default="")
    dst_ip: Mapped[str] = mapped_column(String, default="", server_default="")
    name: Mapped[str] = mapped_column(String, default="", server_default="")
    severity: Mapped[str] = mapped_column(String, default="", server_default="")
    action: Mapped[str] = mapped_column(String, default="", server_default="")
    attributes: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
```

- [ ] **Step 2: Write the `IngestCursor` model**

Create `app/models/ingest_cursor.py`:
```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class IngestCursor(Base):
    """Per-(device, source) ingest watermark. Internal worker state, NOT user-facing
    (no RLS): never exposed via API."""

    __tablename__ = "ingest_cursors"

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(String, primary_key=True)
    last_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_ref: Mapped[str | None] = mapped_column(String, default=None)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 3: Export the models**

In `app/models/__init__.py`, add imports for the new models alongside existing ones (so that `Base.metadata` includes them for `create_all`/autogenerate). Follow the file style (e.g. `from app.models.event import Event` and `from app.models.ingest_cursor import IngestCursor`, and add them to `__all__` if present).

- [ ] **Step 4: Add `events` to RLS**

In `app/core/rls.py`, update the `TENANT_TABLES` line:
```python
TENANT_TABLES: list[str] = ["devices", "metrics", "alerts", "events"]
```
(`ingest_cursors` must NOT be added: it is internal worker state, not exposed via API.)

- [ ] **Step 5: Update conftest (events hypertable)**

In `tests/conftest.py`, in the `db_engine` fixture, immediately after the line
`await conn.execute(text("SELECT create_hypertable('metrics', 'time', if_not_exists => true)"))`
add:
```python
await conn.execute(text("SELECT create_hypertable('events', 'time', if_not_exists => true)"))
```
(Order: `create_all` → create_hypertable metrics → create_hypertable events → `enable_rls_statements()` → role+grant. `enable_rls_statements()` now covers `events` as well.)

- [ ] **Step 6: Write migration 0008**

Create `migrations/versions/0008_events_ingest.py`:
```python
"""events hypertable + ingest_cursors + RLS on events"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.db_roles import APP_ROLE, grant_app_role_statements
from app.core.rls import POLICY_NAME, policy_create_statement

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # events (hypertable)
    op.create_table(
        "events",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("event_key", sa.String(), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(), nullable=False, server_default=""),
        sa.Column("src_ip", sa.String(), nullable=False, server_default=""),
        sa.Column("dst_ip", sa.String(), nullable=False, server_default=""),
        sa.Column("name", sa.String(), nullable=False, server_default=""),
        sa.Column("severity", sa.String(), nullable=False, server_default=""),
        sa.Column("action", sa.String(), nullable=False, server_default=""),
        sa.Column("attributes", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.PrimaryKeyConstraint("time", "device_id", "source", "event_key"),
    )
    op.execute("SELECT create_hypertable('events', 'time')")
    op.create_index(
        "ix_events_tenant_device_source_time",
        "events",
        ["tenant_id", "device_id", "source", "time"],
    )
    op.execute("SELECT add_retention_policy('events', INTERVAL '90 days')")

    # ingest_cursors (worker state, no RLS)
    op.create_table(
        "ingest_cursors",
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("last_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ref", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("device_id", "source"),
    )

    # RLS on events + grant to opngms_app (with propagation to Timescale chunks)
    op.execute("ALTER TABLE events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE events FORCE ROW LEVEL SECURITY")
    op.execute(policy_create_statement("events"))
    for stmt in grant_app_role_statements():
        op.execute(stmt)
    op.execute(f"GRANT SELECT ON events TO {APP_ROLE}")  # propagates to hypertable chunks
    # ingest_cursors is not user-facing: no RLS.


def downgrade() -> None:
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON events FROM {APP_ROLE}")
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON events")
    op.execute("ALTER TABLE events NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE events DISABLE ROW LEVEL SECURITY")
    op.drop_table("ingest_cursors")
    op.execute("SELECT remove_retention_policy('events', if_exists => true)")
    op.drop_table("events")
```

- [ ] **Step 7: Write the model test + RLS isolation**

Create `tests/test_event_model.py` (insert + read as owner):
```python
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context


async def test_event_insert_and_dedup(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    did = uuid.uuid4()
    async with factory() as s:  # owner -> bypasses RLS
        for _ in range(2):  # two identical inserts -> dedup via PK
            await s.execute(
                text(
                    "INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip) "
                    "VALUES (:t, :d, 'ids', 'k1', :tid, 'ET SCAN', '10.0.0.5') "
                    "ON CONFLICT DO NOTHING"
                ),
                {"t": now, "d": did, "tid": tenant_a},
            )
        await s.commit()
        n = (await s.execute(text("SELECT count(*) FROM events"))).scalar_one()
    assert n == 1  # the second insert was deduplicated
```

In `tests/test_rls_isolation.py`, extend `test_rls_statements_cover_metrics_and_alerts` (or add a test) to include `events`:
```python
def test_rls_statements_cover_events():
    assert "events" in TENANT_TABLES
    sql = "\n".join(enable_rls_statements())
    assert "ALTER TABLE events ENABLE ROW LEVEL SECURITY" in sql
    assert "ALTER TABLE events FORCE ROW LEVEL SECURITY" in sql
```
And a raw isolation test (mirror of `test_metrics_alerts_isolated_cross_tenant`):
```python
async def test_events_isolated_cross_tenant(db_engine, two_tenants):
    import os
    import uuid as _uuid
    from datetime import datetime, timezone

    tenant_a, tenant_b = two_tenants
    owner = async_sessionmaker(db_engine, expire_on_commit=False)
    async with owner() as s:  # owner bypasses RLS, inserts for both tenants
        for tid, key in ((tenant_a, "a"), (tenant_b, "b")):
            await s.execute(
                text(
                    "INSERT INTO events (time, device_id, source, event_key, tenant_id, name) "
                    "VALUES (:t, :d, 'ids', :k, :tid, 'sig')"
                ),
                {"t": datetime.now(timezone.utc), "d": _uuid.uuid4(), "k": key, "tid": tid},
            )
        await s.commit()

    base_url = make_url(os.environ["TEST_DATABASE_URL"])
    app_url = base_url.set(username=APP_ROLE, password=APP_ROLE_PASSWORD)
    engine = make_engine(app_url.render_as_string(hide_password=False))
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            await set_tenant_context(s, tenant_a)
            keys = (await s.execute(text("SELECT event_key FROM events"))).scalars().all()
            assert keys == ["a"]  # tenant A only; RLS excludes B (raw query without tenant filter)
        async with factory() as s2:
            assert (await s2.execute(text("SELECT event_key FROM events"))).scalars().all() == []
    finally:
        await engine.dispose()
```
(`make_url`/`make_engine`/`APP_ROLE`/`APP_ROLE_PASSWORD`/`set_tenant_context` are already imported in the file.)

- [ ] **Step 8: Run tests + alembic check**

Run: `... pytest tests/test_event_model.py tests/test_rls_isolation.py -v` → all PASS.
Run: full suite `... pytest -q` → green (127 + new tests).
Run: `alembic check` procedure on a clean DB (upgrade head → check) → "No new upgrade operations detected." Also verify the 0008 downgrade/upgrade round-trip.

- [ ] **Step 9: Commit**
```bash
git add app/models/event.py app/models/ingest_cursor.py app/models/__init__.py app/core/rls.py \
        migrations/versions/0008_events_ingest.py tests/conftest.py tests/test_event_model.py tests/test_rls_isolation.py
git commit -m "feat(backend): events hypertable + ingest_cursors + RLS (migration 0008)"
```

---

## Task 2: `get_ids_alerts` connector

**Files:**
- Modify: `app/connectors/opnsense/client.py`
- Create: `tests/test_connector_ids.py`

- [ ] **Step 1: Write the respx test (fails)**

Create `tests/test_connector_ids.py`. Mock a *plausible* IDS response (list of alert rows) and verify normalisation:
```python
import httpx
import pytest
import respx

from app.connectors.opnsense.client import OpnsenseClient


@respx.mock
async def test_get_ids_alerts_normalizes():
    payload = {
        "rows": [
            {
                "timestamp": "2026-06-09T12:00:00+00:00",
                "src_ip": "10.0.0.5", "dest_ip": "1.2.3.4",
                "alert": {"signature": "ET SCAN Nmap", "severity": 2, "action": "allowed"},
                "alert_id": "abc123",
            }
        ]
    }
    respx.get(url__regex=r".*/api/ids/service/queryAlerts.*").mock(
        return_value=httpx.Response(200, json=payload)
    )
    client = OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)
    out = await client.get_ids_alerts(since=None)
    assert len(out) == 1
    e = out[0]
    assert e["src_ip"] == "10.0.0.5"
    assert e["dst_ip"] == "1.2.3.4"
    assert e["name"] == "ET SCAN Nmap"
    assert e["severity"] == "2"
    assert e["action"] == "allowed"
    assert e["category"] == "alert"
    assert e["event_key"]  # present (source id or hash)
    assert e["time"].tzinfo is not None  # tz-aware datetime
```
(The file follows the style of `tests/test_connector_network.py`; if needed, import `pytest` and mark async like the other tests.)

- [ ] **Step 2: Run and verify the failure**

Run: `... pytest tests/test_connector_ids.py -v` → FAIL (`get_ids_alerts` does not exist).

- [ ] **Step 3: Implement `get_ids_alerts`**

In `app/connectors/opnsense/client.py`, add (after `get_vpn_status`). Import `datetime` and `hashlib` at the top of the file if not already present.
```python
    async def get_ids_alerts(self, since: "datetime | None" = None) -> list[dict]:
        """Normalised Suricata IDS/IPS alerts.

        NOTE: endpoint `ids/service/queryAlerts` and payload format TO BE VERIFIED
        on a real OPNsense device. Defensive against key variants. `since` is a hint:
        fine filtering and dedup happen downstream (cursor + ON CONFLICT).
        """
        data = await self._get("ids/service/queryAlerts")
        out: list[dict] = []
        for r in data.get("rows", data.get("alerts", [])):
            alert = r.get("alert", {}) if isinstance(r.get("alert"), dict) else {}
            ts = self._parse_ts(r.get("timestamp"))
            name = alert.get("signature") or r.get("signature") or ""
            src = r.get("src_ip", "")
            dst = r.get("dest_ip", r.get("dst_ip", ""))
            action = alert.get("action", r.get("action", ""))
            severity = str(alert.get("severity", r.get("severity", "")))
            key = r.get("alert_id") or r.get("_id") or self._event_key(ts, src, dst, name, severity)
            out.append({
                "time": ts,
                "category": "alert",
                "src_ip": src,
                "dst_ip": dst,
                "name": name,
                "severity": severity,
                "action": action,
                "event_key": str(key),
                "attributes": r,
            })
        return out

    @staticmethod
    def _parse_ts(value) -> "datetime":
        from datetime import datetime, timezone

        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return datetime.now(timezone.utc)

    @staticmethod
    def _event_key(ts, *parts) -> str:
        import hashlib

        h = hashlib.sha1("|".join([ts.isoformat(), *[str(p) for p in parts]]).encode())
        return h.hexdigest()
```

- [ ] **Step 4: Run and verify the pass**

Run: `... pytest tests/test_connector_ids.py -v` → PASS.

- [ ] **Step 5: Commit**
```bash
git add app/connectors/opnsense/client.py tests/test_connector_ids.py
git commit -m "feat(backend): connector get_ids_alerts (Suricata alert normalisation)"
```

---

## Task 3: Ingest service (cursor, dedup, IDS)

**Files:**
- Create: `app/services/ingest.py`
- Create: `tests/test_ingest.py`

- [ ] **Step 1: Write the tests (they fail)**

Create `tests/test_ingest.py`. Use an injected fake client (no HTTP). Verify: event writes, cursor advancement, **idempotency** (re-run does not duplicate), **resilience** (source error does not raise).
```python
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.connectors.opnsense.client import ReachabilityError
from app.models.device import Device
from app.services.ingest import ingest_events


class FakeClient:
    def __init__(self, alerts, fail=False):
        self._alerts = alerts
        self._fail = fail

    async def get_ids_alerts(self, since=None):
        if self._fail:
            raise ReachabilityError("boom")
        return self._alerts


def _alert(ts, key, src="10.0.0.5", name="ET SCAN"):
    return {
        "time": ts, "category": "alert", "src_ip": src, "dst_ip": "1.2.3.4",
        "name": name, "severity": "2", "action": "allowed", "event_key": key, "attributes": {},
    }


async def _device(db_engine, tenant_id) -> Device:
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
        await s.commit()
    return did


async def test_ingest_writes_events_and_advances_cursor(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        client = FakeClient([_alert(now, "k1"), _alert(now, "k2")])
        n = await ingest_events(s, device, client, now)
        await s.commit()
    assert n == 2
    async with factory() as s:
        cnt = (await s.execute(text("SELECT count(*) FROM events"))).scalar_one()
        cur = (await s.execute(
            text("SELECT last_time FROM ingest_cursors WHERE device_id=:d AND source='ids'"),
            {"d": did},
        )).scalar_one()
    assert cnt == 2
    assert cur == now


async def test_ingest_idempotent(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    for _ in range(2):  # two runs with the same events
        async with factory() as s:
            device = await s.get(Device, did)
            await ingest_events(s, device, FakeClient([_alert(now, "k1")]), now)
            await s.commit()
    async with factory() as s:
        cnt = (await s.execute(text("SELECT count(*) FROM events"))).scalar_one()
    assert cnt == 1  # no duplicates


async def test_ingest_resilient_to_source_error(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        n = await ingest_events(s, device, FakeClient([], fail=True), now)  # source raises
        await s.commit()
    assert n == 0  # no crash, zero events
```

- [ ] **Step 2: Run and verify the failure**

Run: `... pytest tests/test_ingest.py -v` → FAIL (`app.services.ingest` does not exist).

- [ ] **Step 3: Implement the service**

Create `app/services/ingest.py`:
```python
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense.client import OpnsenseError
from app.models.device import Device
from app.models.event import Event
from app.models.ingest_cursor import IngestCursor

# Active sources: 3B will add "dns".
SOURCES = ["ids"]


async def ingest_events(session: AsyncSession, device: Device, client, now: datetime) -> int:
    """Ingests events (per source) for a device. Returns the number of new events seen.

    Resilient: an error in one source does not block others or raise. Idempotent:
    cursor per (device, source) + insert ON CONFLICT DO NOTHING on the dedup PK.
    """
    total = 0
    for source in SOURCES:
        try:
            total += await _ingest_source(session, device, client, source)
        except OpnsenseError:
            continue  # an unavailable source does not block the others
    return total


async def _ingest_source(session: AsyncSession, device: Device, client, source: str) -> int:
    cursor = await session.get(IngestCursor, (device.id, source))
    since = cursor.last_time if cursor else None
    raw = await _fetch(client, source, since)
    rows = [_normalize(device, source, r) for r in raw]
    if since is not None:
        rows = [r for r in rows if r["time"] > since]  # best-effort client-side
    if not rows:
        return 0
    await session.execute(pg_insert(Event).values(rows).on_conflict_do_nothing())
    new_max = max(r["time"] for r in rows)
    await _advance_cursor(session, device.id, source, new_max)
    return len(rows)


async def _fetch(client, source: str, since):
    if source == "ids":
        return await client.get_ids_alerts(since)
    raise ValueError(f"unknown source: {source}")


def _normalize(device: Device, source: str, r: dict) -> dict:
    return {
        "time": r["time"],
        "device_id": device.id,
        "tenant_id": device.tenant_id,
        "source": source,
        "category": r.get("category", ""),
        "src_ip": r.get("src_ip", ""),
        "dst_ip": r.get("dst_ip", ""),
        "name": r.get("name", ""),
        "severity": r.get("severity", ""),
        "action": r.get("action", ""),
        "event_key": r["event_key"],
        "attributes": r.get("attributes", {}),
    }


async def _advance_cursor(session: AsyncSession, device_id, source: str, new_time: datetime) -> None:
    stmt = (
        pg_insert(IngestCursor)
        .values(device_id=device_id, source=source, last_time=new_time)
        .on_conflict_do_update(
            index_elements=["device_id", "source"],
            set_={"last_time": new_time},
        )
    )
    await session.execute(stmt)
```

- [ ] **Step 4: Run and verify the pass**

Run: `... pytest tests/test_ingest.py -v` → PASS (3/3). Then the full suite is green.

- [ ] **Step 5: Commit**
```bash
git add app/services/ingest.py tests/test_ingest.py
git commit -m "feat(backend): ingest_events service (cursor + ON CONFLICT dedup, IDS source)"
```

---

## Task 4: Worker wiring (cron + job)

**Files:**
- Modify: `app/worker.py`
- Modify: `tests/test_worker_config.py`

- [ ] **Step 1: Write/extend the wiring test (fails)**

In `tests/test_worker_config.py`, add a test that verifies the worker exposes the ingest function and cron. Adapt to the style of the existing file (which already tests `WorkerSettings`):
```python
def test_worker_exposes_event_ingest():
    from app.worker import WorkerSettings, ingest_device_events

    assert ingest_device_events in WorkerSettings.functions
    # two crons: poll metrics + ingest events
    assert len(WorkerSettings.cron_jobs) >= 2
```

- [ ] **Step 2: Run and verify the failure**

Run: `... pytest tests/test_worker_config.py -v` → FAIL (`ingest_device_events` does not exist).

- [ ] **Step 3: Implement the wiring**

In `app/worker.py`:
- import: `from app.services.ingest import ingest_events`.
- add the two functions (mirror of `enqueue_device_polls`/`poll_device`):
```python
async def enqueue_event_ingests(ctx: dict) -> int:
    """Cron: enqueues one ingest_device_events per device."""
    factory = ctx["session_factory"]
    redis = ctx["redis"]
    async with factory() as session:
        ids = (await session.execute(select(Device.id))).scalars().all()
    for device_id in ids:
        await redis.enqueue_job("ingest_device_events", str(device_id))
    return len(ids)


async def ingest_device_events(ctx: dict, device_id: str) -> int:
    """Job: ingests events (IDS) for a single device."""
    factory = ctx["session_factory"]
    async with factory() as session:
        device = await session.get(Device, uuid.UUID(device_id))
        if device is None:
            return 0
        client = OpnsenseClient(
            device.base_url,
            crypto.decrypt(device.api_key_enc),
            crypto.decrypt(device.api_secret_enc),
            verify_tls=device.verify_tls,
        )
        n = await ingest_events(session, device, client, now=datetime.now(timezone.utc))
        await session.commit()
        return n
```
- update `WorkerSettings`:
```python
class WorkerSettings:
    functions = [poll_device, ingest_device_events]
    cron_jobs = [
        cron(enqueue_device_polls, second={0}),               # metrics, every minute
        cron(enqueue_event_ingests, minute=set(range(0, 60, 5))),  # events, every 5 minutes
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
```

- [ ] **Step 4: Run and verify the pass**

Run: `... pytest tests/test_worker_config.py -v` → PASS. Then the full suite is green.

- [ ] **Step 5: Commit**
```bash
git add app/worker.py tests/test_worker_config.py
git commit -m "feat(backend): worker — cron enqueue_event_ingests + job ingest_device_events"
```

---

## Task 5: Technical debt

- [ ] **Step 1: Record 3A debt**

Append to this plan:
```markdown
## Technical debt (3A)

- **OPNsense IDS endpoint TO BE VERIFIED**: `ids/service/queryAlerts` and the payload format are
  plausible but not confirmed on a real device. The connector is defensive against key variants;
  to be refined with the real device (likely pagination/server-side filter for `since`).
- **`since` client-side only**: the ingest filters `time > last_time` client-side after the fetch; without
  server-side filter/pagination the recent window is re-fetched every run (dedup avoids duplicates
  but there is redundant work). Add server-side filter when the real endpoint is known.
- **No cursor delta overlap**: late-arriving events with `time <= last_time` not previously seen would
  be skipped. Acceptable for periodic reports; consider a small overlap + dedup.
- **Fixed ingest cadence (5 min)**: the cron uses a fixed minute set; make
  `INGEST_INTERVAL_SECONDS` configurable (currently hardcoded).
- **Hypertable compression absent**: only 90-day retention. Add Timescale compression policy for
  event volume.
- **`event_key` content hash** when the source provides no stable id: two identical events at the
  same instant collapse into one (acceptable). Prefer source id when available.
```

- [ ] **Step 2: Commit**
```bash
git add docs/superpowers/plans/2026-06-09-opngms-phase3-milestone3A-ingest-suricata.md
git commit -m "docs: technical debt milestone 3A"
```

---

## Definition of "done" (3A)
- The `events` hypertable exists, tenant-isolated by RLS (raw cross-tenant test), with PK-based dedup.
- The `get_ids_alerts` connector normalises Suricata alerts (respx).
- `ingest_events` writes IDS events, advances the cursor, is idempotent and resilient to source errors.
- The worker exposes the `enqueue_event_ingests` cron + the `ingest_device_events` job.
- Green suite + clean `alembic check`.

---

## Technical debt (3A) — consolidated from reviews

- **OPNsense IDS endpoint TO BE VERIFIED**: `ids/service/queryAlerts` and the payload format are
  plausible but not confirmed on a real device. The connector is defensive against key variants;
  to be refined with the real device (likely POST/pagination/server-side filter for `since`).
- **`since` client-side only**: the ingest filters `time > last_time` client-side after the fetch; without
  server-side filter/pagination the recent window is re-fetched every run (dedup avoids duplicates
  but there is redundant work). Add server-side filter when the real endpoint is known. (`since` is
  accepted by the connector but ignored — review Task 2.)
- **No cursor delta overlap**: late-arriving events with `time <= last_time` not previously seen would
  be skipped. Acceptable for periodic reports; consider a small overlap + dedup.
- **Fixed ingest cadence (5 min)**: the cron uses a fixed minute set; make
  `INGEST_INTERVAL_SECONDS` configurable (currently hardcoded).
- **Hypertable compression absent**: only 90-day retention. Add Timescale compression policy for
  event volume.
- **`event_key` content hash** when the source provides no stable id: two truly identical events at the
  same instant collapse into one (acceptable, intended dedup). Prefer source id when available
  (already done: `alert_id`/`_id` → fallback hash).
- **`_normalize` hard-indexes `r["time"]`/`r["event_key"]`** (review Task 3): a connector contract
  change would give a KeyError. Acceptable (fail-fast on malformed payload).
- **`now` unused in `ingest_events`**: kept in signature for consistency with the poller; consider
  whether to use it for the watermark or remove it.
