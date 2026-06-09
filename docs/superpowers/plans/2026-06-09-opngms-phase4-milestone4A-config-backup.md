# OPNGMS — Phase 4 / Milestone 4A: Config Backup + Drift — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Periodically capture versioned, encrypted snapshots of each device's `config.xml`, detect drift (version/plugin-tolerant), and expose version history + a secret-safe per-path structural diff + a drift summary via a tenant-scoped, RLS-isolated API. Read-only — no firewall mutation.

**Architecture:** Reuses the established stack. A daily ARQ cron (`enqueue_config_backups`) enqueues `backup_device_config(device_id)`; the job fetches the raw `config.xml` via `OpnsenseClient` (SSRF-guarded), canonicalizes it (volatile-stripped, schema-agnostic), and inserts a `config_snapshots` row **only when the canonical hash changed** (dedup-on-change), storing the XML gzip+Fernet-encrypted and tagged with the device's OPNsense version. The API (`opngms_app`, RLS) serves snapshot metadata, a path-level structural diff (no values), and a drift summary.

**Tech Stack:** Python 3.12+, FastAPI/SQLAlchemy 2.0 async, Postgres + RLS, ARQ + Redis, Fernet (cryptography), **`defusedxml`** (hardened XML parsing), pytest + respx.

> **Security — untrusted XML:** the `config.xml` comes from a device that could be compromised/hostile.
> Python's stdlib `xml.etree.ElementTree` is vulnerable to **XXE** (external entities) and
> **billion-laughs** (entity expansion). All parsing of config XML MUST use **`defusedxml`**
> (`from defusedxml.ElementTree import fromstring`), never `xml.etree.ElementTree.fromstring` on
> device input. A malformed/hostile config must be **refused and skipped**, never crash the job or
> expand. `defusedxml` is a new backend dependency — add it to `pyproject.toml` and install it.

---

## Context for the implementer (read first)

The whole codebase is **in English** — write all code, comments, docstrings, and messages in **English**. Phases 1–3 are in `main`.

- **Model + RLS reference**: `app/models/alert.py` (normal table, `UUIDPKMixin`, FK to devices CASCADE, indexes, `server_default`), `app/core/rls.py` (`TENANT_TABLES`, currently `["devices","metrics","alerts","events"]`), `migrations/versions/0007_rls_metrics_alerts.py` and `0008_events_ingest.py` (table + RLS enable/force/policy + grant to `opngms_app`).
- **Connector**: `app/connectors/opnsense/client.py` — `_get(path)` does an SSRF-guarded GET + `resp.json()`. 4A refactors the guarded fetch into a shared `_request(path) -> httpx.Response` so a raw-text method can reuse it. Errors: `OpnsenseError` and subclasses (`ReachabilityError`/`AuthError`/`ApiError`/`ParseError`).
- **Crypto**: `app/core/crypto.py` — `encrypt(str)->bytes` / `decrypt(bytes)->str` over Fernet. 4A adds `encrypt_bytes(bytes)->bytes` / `decrypt_bytes(bytes)->bytes` for binary (gzipped) content.
- **Worker**: `app/worker.py` — `enqueue_device_polls`/`poll_device` and `enqueue_event_ingests`/`ingest_device_events` + `WorkerSettings` (functions + cron_jobs). Mirror for config backup.
- **Ingest service pattern**: `app/services/ingest.py` — resilient per-device, owner connection, dedup. Mirror the spirit.
- **API/repository/schema reference (2C/3C)**: `app/api/events.py`, `app/repositories/event.py`, `app/schemas/event.py`; tenant gating via `require_tenant(Action.DEVICE_VIEW)`; isolation tests in `tests/test_events_rls_api.py` (real `opngms_app`, raw-SQL RLS proof).
- **conftest**: `tests/conftest.py` — `config_snapshots` is a normal table (NOT a hypertable), so it is created by `create_all` and covered by `enable_rls_statements()` once it is in `TENANT_TABLES`; no `create_hypertable` needed. Fixtures: `db_engine`, `two_tenants`, `api_client`, `app_role_api_client`.

**Test command** (from `backend/`):
```
TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" \
ADMIN_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" \
.venv/bin/python -m pytest -q
```
Current suite: **158 tests green**. For `alembic check` on a clean DB, follow the 2C/3A procedure (create `opngms_check` + timescaledb extension, `upgrade head`, `check`, drop; env `SESSION_SECRET`/`MASTER_KEY`).

**Security guardrails (do not violate):**
- Snapshot content is **encrypted at rest** (gzip then Fernet). Never store the raw XML in cleartext.
- The API returns metadata + a **per-path structural diff WITHOUT values**. It must never emit element
  values (they may be secrets). No raw-config download endpoint in 4A.
- Canonicalization must **preserve element order** (firewall rules are order-sensitive); only strip the
  known-volatile `<revision>` node. Do NOT sort siblings.

⚠️ **OPNsense backup endpoint `/api/core/backup/download/this` TO VERIFY** against a real device; mocked with respx. Abstraction + tests do not change.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `app/models/config_snapshot.py` | `ConfigSnapshot` model | Create |
| `app/models/__init__.py` | export the model | Modify |
| `app/core/rls.py` | add `"config_snapshots"` to `TENANT_TABLES` | Modify |
| `migrations/versions/0009_config_snapshots.py` | table + RLS + grant | Create |
| `app/core/crypto.py` | `encrypt_bytes` / `decrypt_bytes` | Modify |
| `app/connectors/opnsense/client.py` | `_request` refactor + `get_config_backup` | Modify |
| `app/services/config_diff.py` | `canonical_hash`, `structural_diff` (pure) | Create |
| `app/services/config_backup.py` | `backup_config` (dedup-on-change, encrypt) | Create |
| `app/worker.py` | cron `enqueue_config_backups` + job `backup_device_config` | Modify |
| `app/schemas/config.py` | `ConfigSnapshotOut`, `ConfigDiffEntry`, `DriftSummary` | Create |
| `app/repositories/config_snapshot.py` | `ConfigSnapshotRepository` | Create |
| `app/api/config.py` | snapshots / diff / drift endpoints | Create |
| `app/main.py` | register router | Modify |
| tests (see tasks) | model+RLS, connector, diff, backup, API+isolation | Create/Modify |

---

## Task 1: `config_snapshots` model + migration + RLS

**Files:**
- Create: `app/models/config_snapshot.py`; Modify: `app/models/__init__.py`, `app/core/rls.py`
- Create: `migrations/versions/0009_config_snapshots.py`
- Create: `tests/test_config_snapshot_model.py`; Modify: `tests/test_rls_isolation.py`

- [ ] **Step 1: Write the model**

Create `app/models/config_snapshot.py` (mirror `alert.py`; normal table):
```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class ConfigSnapshot(UUIDPKMixin, Base):
    __tablename__ = "config_snapshots"
    __table_args__ = (
        Index("ix_config_snapshots_tenant_device_taken", "tenant_id", "device_id", "taken_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    canonical_hash: Mapped[str] = mapped_column(String)
    content_enc: Mapped[bytes] = mapped_column(LargeBinary)  # Fernet(gzip(config.xml))
    opnsense_version: Mapped[str] = mapped_column(String, default="", server_default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
```

- [ ] **Step 2: Export the model**

In `app/models/__init__.py`, add `from app.models.config_snapshot import ConfigSnapshot` and add it to `__all__` (follow the file's style).

- [ ] **Step 3: Add to RLS**

In `app/core/rls.py`:
```python
TENANT_TABLES: list[str] = ["devices", "metrics", "alerts", "events", "config_snapshots"]
```

- [ ] **Step 4: Write the migration**

Create `migrations/versions/0009_config_snapshots.py` (mirror 0008's table + RLS block; no hypertable):
```python
"""config_snapshots table + RLS"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.db_roles import APP_ROLE, grant_app_role_statements
from app.core.rls import POLICY_NAME, policy_create_statement

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "config_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("taken_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("canonical_hash", sa.String(), nullable=False),
        sa.Column("content_enc", sa.LargeBinary(), nullable=False),
        sa.Column("opnsense_version", sa.String(), nullable=False, server_default=""),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_config_snapshots_tenant_id", "config_snapshots", ["tenant_id"])
    op.create_index("ix_config_snapshots_device_id", "config_snapshots", ["device_id"])
    op.create_index(
        "ix_config_snapshots_tenant_device_taken",
        "config_snapshots", ["tenant_id", "device_id", "taken_at"],
    )
    op.execute("ALTER TABLE config_snapshots ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE config_snapshots FORCE ROW LEVEL SECURITY")
    op.execute(policy_create_statement("config_snapshots"))
    for stmt in grant_app_role_statements():
        op.execute(stmt)


def downgrade() -> None:
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON config_snapshots FROM {APP_ROLE}")
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON config_snapshots")
    op.execute("ALTER TABLE config_snapshots NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE config_snapshots DISABLE ROW LEVEL SECURITY")
    op.drop_table("config_snapshots")
```

- [ ] **Step 5: Write the model + RLS isolation tests**

Create `tests/test_config_snapshot_model.py` (insert + read as owner):
```python
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker


async def test_config_snapshot_insert(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:  # owner -> bypasses RLS
        await s.execute(
            text(
                "INSERT INTO config_snapshots (id, tenant_id, device_id, canonical_hash, content_enc) "
                "VALUES (:id, :tid, :did, 'h1', '\\x00'::bytea)"
            ),
            {"id": uuid.uuid4(), "tid": tenant_a, "did": uuid.uuid4()},
        )
        await s.commit()
        n = (await s.execute(text("SELECT count(*) FROM config_snapshots"))).scalar_one()
    assert n == 1
```

In `tests/test_rls_isolation.py` add (mirror `test_events_isolated_cross_tenant`):
```python
async def test_config_snapshots_isolated_cross_tenant(db_engine, two_tenants):
    import os
    import uuid as _uuid

    tenant_a, tenant_b = two_tenants
    owner = async_sessionmaker(db_engine, expire_on_commit=False)
    async with owner() as s:  # owner bypasses RLS
        for tid, h in ((tenant_a, "a"), (tenant_b, "b")):
            await s.execute(
                text(
                    "INSERT INTO config_snapshots (id, tenant_id, device_id, canonical_hash, content_enc) "
                    "VALUES (:id, :tid, :did, :h, '\\x00'::bytea)"
                ),
                {"id": _uuid.uuid4(), "tid": tid, "did": _uuid.uuid4(), "h": h},
            )
        await s.commit()

    base_url = make_url(os.environ["TEST_DATABASE_URL"])
    app_url = base_url.set(username=APP_ROLE, password=APP_ROLE_PASSWORD)
    engine = make_engine(app_url.render_as_string(hide_password=False))
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            await set_tenant_context(s, tenant_a)
            hs = (await s.execute(text("SELECT canonical_hash FROM config_snapshots"))).scalars().all()
            assert hs == ["a"]  # RLS hides B (raw query, no tenant filter)
        async with factory() as s2:
            assert (await s2.execute(text("SELECT canonical_hash FROM config_snapshots"))).scalars().all() == []
    finally:
        await engine.dispose()
```
Also add a static check that `"config_snapshots" in TENANT_TABLES`.

- [ ] **Step 6: Run tests + alembic check**

Run: `... pytest tests/test_config_snapshot_model.py tests/test_rls_isolation.py -v` → PASS.
Run: whole suite green. `alembic check` on clean DB → "No new upgrade operations detected"; verify the 0009 downgrade/upgrade round-trip.

- [ ] **Step 7: Commit**
```bash
git add app/models/config_snapshot.py app/models/__init__.py app/core/rls.py \
        migrations/versions/0009_config_snapshots.py tests/test_config_snapshot_model.py tests/test_rls_isolation.py
git commit -m "feat(backend): config_snapshots table + RLS (migration 0009)"
```

---

## Task 2: Connector `get_config_backup` (raw-text SSRF-guarded fetch)

**Files:**
- Modify: `app/connectors/opnsense/client.py`
- Create: `tests/test_connector_config.py`

- [ ] **Step 1: Write the failing respx test**

Create `tests/test_connector_config.py`:
```python
import httpx
import respx

from app.connectors.opnsense.client import OpnsenseClient

XML = "<opnsense><system><hostname>fw1</hostname></system></opnsense>"


@respx.mock
async def test_get_config_backup_returns_raw_xml():
    respx.get(url__regex=r".*/api/core/backup/download/this.*").mock(
        return_value=httpx.Response(200, text=XML, headers={"content-type": "application/xml"})
    )
    client = OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)
    out = await client.get_config_backup()
    assert out == XML
    assert "<hostname>fw1</hostname>" in out
```

- [ ] **Step 2: Run and verify it fails**

Run: `... pytest tests/test_connector_config.py -v` → FAIL (`get_config_backup` missing).

- [ ] **Step 3: Refactor `_get` to share the guarded fetch, add `get_config_backup`**

In `app/connectors/opnsense/client.py`, extract the SSRF-guarded request (everything from the URL build through the status-code checks) into a private `_request`, and make `_get` call it. **Preserve behavior** — the existing connector tests must stay green.
```python
    async def _request(self, path: str) -> "httpx.Response":
        # SSRF guard: validate scheme/userinfo/host and resolve+pin the IP.
        try:
            pinned_ip, host, port = validate_base_url(self._base_url)
        except UnsafeUrlError as exc:
            raise ReachabilityError("unsafe destination") from exc
        conn_host = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
        netloc = conn_host if port is None else f"{conn_host}:{port}"
        base_path = urlsplit(self._base_url).path.rstrip("/")
        url = f"https://{netloc}{base_path}/api/{path.lstrip('/')}"
        try:
            async with httpx.AsyncClient(
                verify=self._verify, timeout=self._timeout, auth=self._auth, follow_redirects=False
            ) as client:
                resp = await client.get(
                    url, headers={"Host": host}, extensions={"sni_hostname": host}
                )
        except httpx.HTTPError as exc:
            raise ReachabilityError("device unreachable") from exc
        if resp.status_code in (401, 403):
            raise AuthError(f"auth failed: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise ApiError(resp.status_code)
        return resp

    async def _get(self, path: str) -> dict:
        resp = await self._request(path)
        try:
            return resp.json()
        except ValueError as exc:
            raise ParseError("unparseable response") from exc

    async def get_config_backup(self) -> str:
        """Download the raw config.xml as text. NOTE: endpoint
        `core/backup/download/this` TO VERIFY against a real OPNsense device."""
        resp = await self._request("core/backup/download/this")
        return resp.text
```
(Keep the existing module docstring/comments; just restructure. The `import httpx` is already at module top — drop the string annotation if preferred.)

- [ ] **Step 4: Run tests and verify**

Run: `... pytest tests/test_connector_config.py tests/test_connector_ids.py tests/test_connector_network.py -v` → all PASS (new + existing connector tests prove the refactor preserved behavior). Then the whole suite green.

- [ ] **Step 5: Commit**
```bash
git add app/connectors/opnsense/client.py tests/test_connector_config.py
git commit -m "feat(backend): connector get_config_backup (raw-text SSRF-guarded fetch)"
```

---

## Task 3: Canonicalization + structural diff (pure functions)

**Files:**
- Modify: `pyproject.toml` (add `defusedxml`)
- Create: `app/services/config_diff.py`
- Create: `tests/test_config_diff.py`

- [ ] **Step 0: Add the `defusedxml` dependency**

Add `defusedxml` to `pyproject.toml` `dependencies`, then install into the venv:
```bash
.venv/bin/pip install defusedxml
```
(Confirm `from defusedxml.ElementTree import fromstring` imports.)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config_diff.py`:
```python
from app.services.config_diff import canonical_hash, structural_diff

BASE = (
    "<opnsense>"
    "<revision><time>1000</time><description>save A</description></revision>"
    "<system><hostname>fw1</hostname><user><password>secret1</password></user></system>"
    "</opnsense>"
)
# Only <revision> changed (re-save) -> must NOT count as drift.
RESAVED = (
    "<opnsense>"
    "<revision><time>2000</time><description>save B</description></revision>"
    "<system><hostname>fw1</hostname><user><password>secret1</password></user></system>"
    "</opnsense>"
)
# A real change: hostname + password changed.
CHANGED = (
    "<opnsense>"
    "<revision><time>3000</time><description>save C</description></revision>"
    "<system><hostname>fw2</hostname><user><password>secret2</password></user></system>"
    "</opnsense>"
)


def test_canonical_hash_ignores_revision_only_changes():
    assert canonical_hash(BASE) == canonical_hash(RESAVED)


def test_canonical_hash_detects_real_change():
    assert canonical_hash(BASE) != canonical_hash(CHANGED)


def test_structural_diff_reports_paths_without_values():
    changes = structural_diff(BASE, CHANGED)
    paths = {c["path"]: c["change"] for c in changes}
    assert paths["opnsense/system/hostname"] == "modified"
    assert paths["opnsense/system/user/password"] == "modified"
    # Secret-safe: no element values appear anywhere in the output.
    blob = repr(changes)
    assert "secret1" not in blob and "secret2" not in blob and "fw2" not in blob


def test_structural_diff_added_removed():
    a = "<opnsense><system><hostname>fw1</hostname></system></opnsense>"
    b = "<opnsense><system><hostname>fw1</hostname><dnsserver>1.1.1.1</dnsserver></system></opnsense>"
    changes = {c["path"]: c["change"] for c in structural_diff(a, b)}
    assert changes["opnsense/system/dnsserver"] == "added"
    changes2 = {c["path"]: c["change"] for c in structural_diff(b, a)}
    assert changes2["opnsense/system/dnsserver"] == "removed"


def test_rejects_billion_laughs_entity_expansion():
    import pytest

    bomb = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
        "<opnsense><x>&lol2;</x></opnsense>"
    )
    # defusedxml must refuse entity expansion (raise), not expand it.
    with pytest.raises(Exception):
        canonical_hash(bomb)


def test_rejects_external_entity_xxe():
    import pytest

    xxe = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        "<opnsense><x>&xxe;</x></opnsense>"
    )
    with pytest.raises(Exception):
        canonical_hash(xxe)
```

- [ ] **Step 2: Run and verify it fails**

Run: `... pytest tests/test_config_diff.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement the service**

Create `app/services/config_diff.py`. Schema-agnostic, order-preserving, secret-safe.
```python
"""Version/plugin-tolerant config comparison.

Pure functions over config.xml strings: a canonical hash that ignores the volatile
OPNsense <revision> metadata, and a per-path structural diff that reports WHICH element
paths changed (added/removed/modified) WITHOUT emitting their values (which may be secrets).
Element order is preserved (firewall rules are order-sensitive): repeated siblings are
indexed by position; siblings are never sorted.
"""

import hashlib
import xml.etree.ElementTree as ET  # Element type annotations only — NOT for parsing

from defusedxml.ElementTree import fromstring as _parse_xml  # XXE / billion-laughs safe

# Known-volatile top-level nodes that change on every save without a real config change.
_VOLATILE_TAGS = frozenset({"revision"})


def _strip_volatile(root: ET.Element) -> None:
    for child in list(root):
        if child.tag in _VOLATILE_TAGS:
            root.remove(child)


def _flatten(xml: str) -> dict[str, str]:
    """Map every leaf element / attribute to its value, keyed by an indexed path.

    Parses with defusedxml: hostile XML (XXE / billion-laughs) is refused (raises),
    never expanded. Callers treat a raise as "skip this config".
    """
    root = _parse_xml(xml)
    _strip_volatile(root)
    out: dict[str, str] = {}

    def walk(elem: ET.Element, path: str) -> None:
        for key, val in elem.attrib.items():
            out[f"{path}/@{key}"] = val
        children = list(elem)
        if not children:
            out[path] = (elem.text or "").strip()
            return
        tag_total: dict[str, int] = {}
        for child in children:
            tag_total[child.tag] = tag_total.get(child.tag, 0) + 1
        seen: dict[str, int] = {}
        for child in children:
            seen[child.tag] = seen.get(child.tag, 0) + 1
            seg = child.tag if tag_total[child.tag] == 1 else f"{child.tag}[{seen[child.tag]}]"
            walk(child, f"{path}/{seg}")

    walk(root, root.tag)
    return out


def canonical_hash(xml: str) -> str:
    """sha256 over the volatile-stripped flattened (path, value) pairs."""
    flat = _flatten(xml)
    blob = "\n".join(f"{p}={flat[p]}" for p in sorted(flat))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def structural_diff(xml_a: str, xml_b: str) -> list[dict]:
    """List of {path, change} where change in {added, removed, modified}. No values emitted."""
    a, b = _flatten(xml_a), _flatten(xml_b)
    changes: list[dict] = []
    for path in sorted(set(a) | set(b)):
        if path not in b:
            changes.append({"path": path, "change": "removed"})
        elif path not in a:
            changes.append({"path": path, "change": "added"})
        elif a[path] != b[path]:
            changes.append({"path": path, "change": "modified"})
    return changes
```

- [ ] **Step 4: Run and verify it passes**

Run: `... pytest tests/test_config_diff.py -v` → PASS (4/4).

- [ ] **Step 5: Commit**
```bash
git add app/services/config_diff.py tests/test_config_diff.py
git commit -m "feat(backend): config canonical hash + secret-safe structural diff (schema-agnostic)"
```

---

## Task 4: Backup service + worker wiring

**Files:**
- Modify: `app/core/crypto.py`
- Create: `app/services/config_backup.py`
- Modify: `app/worker.py`
- Create: `tests/test_config_backup.py`; Modify: `tests/test_worker_config.py`

- [ ] **Step 1: Write failing backup-service tests**

Create `tests/test_config_backup.py`. Fake client; verify first-run inserts a snapshot, identical config → no new row (dedup), changed config → new row, connector error → skipped.
```python
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.connectors.opnsense.client import ReachabilityError
from app.core import crypto
from app.models.device import Device
from app.services.config_backup import backup_config

XML1 = "<opnsense><revision><time>1</time></revision><system><hostname>fw1</hostname></system></opnsense>"
XML1B = "<opnsense><revision><time>2</time></revision><system><hostname>fw1</hostname></system></opnsense>"  # re-save only
XML2 = "<opnsense><revision><time>3</time></revision><system><hostname>fw2</hostname></system></opnsense>"  # changed


class FakeClient:
    def __init__(self, xml, fail=False):
        self._xml = xml
        self._fail = fail

    async def get_config_backup(self):
        if self._fail:
            raise ReachabilityError("boom")
        return self._xml


async def _device(db_engine, tenant_id) -> uuid.UUID:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags, firmware_version) "
                "VALUES (:id, :t, 'fw', 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}', '24.7')"
            ),
            {"id": did, "t": tenant_id},
        )
        await s.commit()
    return did


async def test_first_backup_inserts_snapshot(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        created = await backup_config(s, device, FakeClient(XML1))
        await s.commit()
    assert created is True
    async with factory() as s:
        row = (await s.execute(
            text("SELECT content_enc, opnsense_version FROM config_snapshots WHERE device_id=:d"),
            {"d": did},
        )).one()
    # content is encrypted (not the raw XML), version tagged
    assert bytes(row.content_enc) != XML1.encode()
    assert row.opnsense_version == "24.7"


async def test_resave_does_not_create_new_version(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    for xml in (XML1, XML1B):  # only <revision> differs
        async with factory() as s:
            device = await s.get(Device, did)
            await backup_config(s, device, FakeClient(xml))
            await s.commit()
    async with factory() as s:
        n = (await s.execute(text("SELECT count(*) FROM config_snapshots WHERE device_id=:d"), {"d": did})).scalar_one()
    assert n == 1  # dedup-on-change


async def test_real_change_creates_new_version(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    for xml in (XML1, XML2):
        async with factory() as s:
            device = await s.get(Device, did)
            await backup_config(s, device, FakeClient(xml))
            await s.commit()
    async with factory() as s:
        n = (await s.execute(text("SELECT count(*) FROM config_snapshots WHERE device_id=:d"), {"d": did})).scalar_one()
    assert n == 2


async def test_connector_error_skips(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        created = await backup_config(s, device, FakeClient("", fail=True))
        await s.commit()
    assert created is False
    async with factory() as s:
        n = (await s.execute(text("SELECT count(*) FROM config_snapshots WHERE device_id=:d"), {"d": did})).scalar_one()
    assert n == 0


async def test_hostile_xml_is_skipped(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    bomb = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE lolz [<!ENTITY a "x"><!ENTITY b "&a;&a;&a;&a;">]>'
        "<opnsense><x>&b;</x></opnsense>"
    )
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        created = await backup_config(s, device, FakeClient(bomb))  # defusedxml refuses -> skip
        await s.commit()
    assert created is False
    async with factory() as s:
        n = (await s.execute(text("SELECT count(*) FROM config_snapshots WHERE device_id=:d"), {"d": did})).scalar_one()
    assert n == 0
```

- [ ] **Step 2: Run and verify it fails**

Run: `... pytest tests/test_config_backup.py -v` → FAIL (module + crypto helpers missing).

- [ ] **Step 3: Add binary crypto helpers**

In `app/core/crypto.py` add (mirroring `encrypt`/`decrypt`):
```python
def encrypt_bytes(data: bytes) -> bytes:
    return _fernet().encrypt(data)


def decrypt_bytes(token: bytes) -> bytes:
    return _fernet().decrypt(bytes(token))
```

- [ ] **Step 4: Implement the backup service**

Create `app/services/config_backup.py`:
```python
import gzip

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense.client import OpnsenseError
from app.core import crypto
from app.models.config_snapshot import ConfigSnapshot
from app.models.device import Device
from app.services.config_diff import canonical_hash


async def backup_config(session: AsyncSession, device: Device, client) -> bool:
    """Fetch the device config, store a new encrypted snapshot only if it changed.

    Returns True if a new version was stored, False otherwise (no change, or a
    connector error which is swallowed so the cron job survives).
    """
    try:
        xml = await client.get_config_backup()
    except OpnsenseError:
        return False
    try:
        digest = canonical_hash(xml)
    except (ValueError, SyntaxError):
        # Malformed or hostile XML (XXE / billion-laughs refused by defusedxml):
        # skip this device, never crash the job, never store.
        return False
    latest = (
        await session.execute(
            select(ConfigSnapshot.canonical_hash)
            .where(ConfigSnapshot.device_id == device.id)
            .order_by(ConfigSnapshot.taken_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest == digest:
        return False  # dedup-on-change
    content_enc = crypto.encrypt_bytes(gzip.compress(xml.encode("utf-8")))
    session.add(
        ConfigSnapshot(
            tenant_id=device.tenant_id,
            device_id=device.id,
            canonical_hash=digest,
            content_enc=content_enc,
            opnsense_version=device.firmware_version or "",
            size_bytes=len(xml.encode("utf-8")),
        )
    )
    await session.flush()
    return True
```

- [ ] **Step 5: Run the service tests**

Run: `... pytest tests/test_config_backup.py -v` → PASS (4/4).

- [ ] **Step 6: Wire the worker (cron + job)**

In `app/worker.py` add `from app.services.config_backup import backup_config` and:
```python
async def enqueue_config_backups(ctx: dict) -> int:
    """Cron: enqueue a config backup for every device."""
    factory = ctx["session_factory"]
    redis = ctx["redis"]
    async with factory() as session:
        ids = (await session.execute(select(Device.id))).scalars().all()
    for device_id in ids:
        await redis.enqueue_job("backup_device_config", str(device_id))
    return len(ids)


async def backup_device_config(ctx: dict, device_id: str) -> bool:
    """Job: back up a single device's config (dedup-on-change)."""
    factory = ctx["session_factory"]
    async with factory() as session:
        device = await session.get(Device, uuid.UUID(device_id))
        if device is None:
            return False
        client = OpnsenseClient(
            device.base_url,
            crypto.decrypt(device.api_key_enc),
            crypto.decrypt(device.api_secret_enc),
            verify_tls=device.verify_tls,
        )
        created = await backup_config(session, device, client)
        await session.commit()
        return created
```
Update `WorkerSettings`:
```python
class WorkerSettings:
    functions = [poll_device, ingest_device_events, backup_device_config]
    cron_jobs = [
        cron(enqueue_device_polls, second={0}),
        cron(enqueue_event_ingests, minute=set(range(0, 60, 5))),
        cron(enqueue_config_backups, hour={3}, minute={0}),  # daily ~03:00
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
```
In `tests/test_worker_config.py` add a test that `backup_device_config in WorkerSettings.functions` and `len(cron_jobs) >= 3`.

- [ ] **Step 7: Run the whole suite**

Run: `... pytest -q` → all green.

- [ ] **Step 8: Commit**
```bash
git add app/core/crypto.py app/services/config_backup.py app/worker.py \
        tests/test_config_backup.py tests/test_worker_config.py
git commit -m "feat(backend): config backup service (dedup-on-change, encrypted) + worker cron/job"
```

---

## Task 5: Query API (snapshots / diff / drift)

**Files:**
- Create: `app/schemas/config.py`, `app/repositories/config_snapshot.py`, `app/api/config.py`
- Modify: `app/main.py`
- Create: `tests/test_config_api.py`, `tests/test_config_rls_api.py`

- [ ] **Step 1: Write the schemas**

Create `app/schemas/config.py`:
```python
import uuid
from datetime import datetime

from pydantic import BaseModel


class ConfigSnapshotOut(BaseModel):
    id: uuid.UUID
    device_id: uuid.UUID
    taken_at: datetime
    canonical_hash: str
    opnsense_version: str
    size_bytes: int
    # NB: content is NEVER exposed (it holds secrets).

    model_config = {"from_attributes": True}


class ConfigDiffEntry(BaseModel):
    path: str
    change: str  # added | removed | modified


class DriftSummary(BaseModel):
    version_count: int
    latest_taken_at: datetime | None
    changed_since_previous: bool
```

- [ ] **Step 2: Write the repository**

Create `app/repositories/config_snapshot.py`:
```python
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config_snapshot import ConfigSnapshot


class ConfigSnapshotRepository:
    """Tenant-scoped config snapshot reads. Double isolation: tenant_id filter + RLS."""

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def list(self, device_id: uuid.UUID) -> Sequence[ConfigSnapshot]:
        stmt = (
            select(ConfigSnapshot)
            .where(ConfigSnapshot.tenant_id == self.tenant_id, ConfigSnapshot.device_id == device_id)
            .order_by(ConfigSnapshot.taken_at.desc())
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def get(self, snapshot_id: uuid.UUID) -> ConfigSnapshot | None:
        stmt = select(ConfigSnapshot).where(
            ConfigSnapshot.id == snapshot_id, ConfigSnapshot.tenant_id == self.tenant_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
```

- [ ] **Step 3: Write the endpoints + register the router**

Create `app/api/config.py`. The diff endpoint decrypts the two snapshots server-side and returns the structural diff (paths only).
```python
import gzip
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.db import get_session
from app.core.deps import TenantContext, require_tenant
from app.core.rbac import Action
from app.repositories.config_snapshot import ConfigSnapshotRepository
from app.schemas.config import ConfigDiffEntry, ConfigSnapshotOut, DriftSummary
from app.services.config_diff import structural_diff

router = APIRouter(prefix="/api/tenants/{tenant_id}", tags=["config"])


def _xml(snapshot) -> str:
    return gzip.decompress(crypto.decrypt_bytes(snapshot.content_enc)).decode("utf-8")


@router.get("/devices/{device_id}/config/snapshots", response_model=list[ConfigSnapshotOut])
async def list_snapshots(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> list[ConfigSnapshotOut]:
    rows = await ConfigSnapshotRepository(session, tenant_id).list(device_id)
    return [ConfigSnapshotOut.model_validate(r) for r in rows]


@router.get("/devices/{device_id}/config/drift", response_model=DriftSummary)
async def config_drift(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> DriftSummary:
    rows = await ConfigSnapshotRepository(session, tenant_id).list(device_id)
    return DriftSummary(
        version_count=len(rows),
        latest_taken_at=rows[0].taken_at if rows else None,
        changed_since_previous=len(rows) >= 2,
    )


@router.get("/devices/{device_id}/config/diff", response_model=list[ConfigDiffEntry])
async def config_diff(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    from_id: uuid.UUID = Query(..., alias="from"),
    to_id: uuid.UUID = Query(..., alias="to"),
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> list[ConfigDiffEntry]:
    repo = ConfigSnapshotRepository(session, tenant_id)
    a = await repo.get(from_id)
    b = await repo.get(to_id)
    if a is None or b is None or a.device_id != device_id or b.device_id != device_id:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return [ConfigDiffEntry(**c) for c in structural_diff(_xml(a), _xml(b))]
```
Register in `app/main.py`:
```python
from app.api.config import router as config_router
# after the other include_router(...) calls:
app.include_router(config_router)
```

- [ ] **Step 4: Write API + isolation tests**

Create `tests/test_config_api.py` (owner client): seed snapshots via the backup service or raw insert (encrypt with `crypto.encrypt_bytes(gzip.compress(xml.encode()))`), then:
- `GET .../config/snapshots` returns metadata (and **no `content`/`content_enc` field** in the JSON).
- `GET .../config/drift` returns counts.
- `GET .../config/diff?from=&to=` returns path-level changes; assert **no element value** (e.g. a secret) appears in the JSON.
- 401 without session; 403 for a no-membership user.

Create `tests/test_config_rls_api.py` (real `opngms_app`): two tenants each with snapshots; tenant A's `GET .../config/snapshots` returns only A's; add a raw-SQL RLS proof (no tenant filter, context A → only A's rows), like `test_events_rls_api.py`.

- [ ] **Step 5: Run + alembic check**

Run: `... pytest -q` → all green. `alembic check` clean.

- [ ] **Step 6: Commit**
```bash
git add app/schemas/config.py app/repositories/config_snapshot.py app/api/config.py app/main.py \
        tests/test_config_api.py tests/test_config_rls_api.py
git commit -m "feat(backend): config query API (snapshots / structural diff / drift, tenant-scoped + RLS)"
```

---

## Task 6: Technical debt

- [ ] **Step 1: Record the 4A debt**

Append to this plan:
```markdown
## Technical debt (4A)

- **OPNsense backup endpoint TO VERIFY**: `core/backup/download/this` and the response format are
  plausible but unconfirmed; confirm against a real device (response may be wrapped, not raw XML).
- **Volatile-node allowlist**: only `<revision>` is stripped. Refine against real configs if other
  nodes (statistics/caches) prove volatile and cause false drift.
- **Backup cadence fixed (daily ~03:00)**: make `CONFIG_BACKUP_INTERVAL` configurable.
- **No "last checked" timestamp**: dedup means no row is written when unchanged, so "when did we last
  successfully check?" is not recorded. Add a small per-device state row/timestamp if needed.
- **Snapshot retention unbounded**: all versions are kept. Add a retention/prune policy if storage
  grows (config changes are low-volume, so deferred).
- **Diff requires two explicit snapshot ids**: add convenience (`against=previous`, default to
  latest-vs-previous) and validate both belong to the device (already enforced) / tenant.
- **Raw config download intentionally absent** (holds secrets): a gated + audited download is a later
  milestone (elevated role).
```

- [ ] **Step 2: Commit**
```bash
git add docs/superpowers/plans/2026-06-09-opngms-phase4-milestone4A-config-backup.md
git commit -m "docs: technical debt milestone 4A"
```

---

## Definition of "Done" (4A)
- The worker captures versioned, **encrypted** config snapshots per device on cadence, **deduped on change**, tagged with the OPNsense version.
- Drift detection (`canonical_hash`) ignores re-save noise (`<revision>`) and is tolerant of version/plugin differences (schema-agnostic).
- The API exposes snapshot history, a **secret-safe per-path structural diff**, and a drift summary, isolated per customer by RLS (proven by a real-`opngms_app` test).
- Suite green + `alembic check` clean.
