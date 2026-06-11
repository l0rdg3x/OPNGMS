# OPNsense Live Config Push (4D-b / 4D-d) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing dry-run config-push pipeline into a real one for firewall aliases — fix `apply_alias` against the verified write API, gated behind a default-OFF `LIVE_PUSH_ENABLED` master switch, with a pre-apply config snapshot as a rollback point.

**Architecture:** `apply_alias` corrected to the verified firewall/alias write API (uuid-in-path for set/delete via `searchItem`, slow `reconfigure` with a long timeout). `apply_change` (already staleness-guarded + lock-serialized) flips to real apply via `dry_run = not LIVE_PUSH_ENABLED`, persisting the current config as a snapshot before mutating.

**Tech Stack:** Python 3.14, the SSRF-guarded connector, SQLAlchemy/Alembic, pydantic-settings, pytest + respx.

**Spec:** `docs/superpowers/specs/2026-06-11-opnsense-live-config-push-design.md`
**Branch:** `feat/opnsense-live-config-push` (created; spec committed there).

**Run tests:** `cd /home/l0rdg3x/coding/OPNGMS/backend && .venv/bin/python -m pytest <files> -q`. DB-touching tests need env: `TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test`. English files/comments; commit messages end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File Structure

- **Modify:** `backend/app/connectors/opnsense/client.py` (fix `apply_alias` + `_resolve_alias_uuid`; `timeout` override on `_request`/`_post`; `RECONFIGURE_TIMEOUT`), `backend/app/core/config.py` (`live_push_enabled`), `backend/app/services/config_push.py` (`apply_change` flip + `_save_pre_apply_snapshot`), `backend/app/models/config_change.py` (`pre_apply_snapshot_id`).
- **Create:** `backend/migrations/versions/0017_config_change_pre_apply_snapshot.py`, `scripts/verify_live_push.py`, migration test.
- **Modify (tests):** `backend/tests/test_connector_apply_alias.py` (rewrite for the verified API), `backend/tests/test_config_push_apply.py` (add the switch-ON test).

---

## Task 1: Fix `apply_alias` against the verified write API

**Files:** Modify `backend/app/connectors/opnsense/client.py`; rewrite `backend/tests/test_connector_apply_alias.py`.

**Context:** Verified live on OPNsense 26.1.9: `addItem` returns `{"result","uuid"}`; `setItem/<uuid>` and `delItem/<uuid>` need the uuid IN THE PATH; the uuid is found by exact name via `POST firewall/alias/searchItem {searchPhrase}` → `{"rows":[{uuid,name}]}` (substring match, so filter to exact name); `reconfigure` is slow (needs a long timeout). The current `apply_alias` calls set/del without the uuid and reconfigures under the 10s default — both wrong.

- [ ] **Step 1: Rewrite `backend/tests/test_connector_apply_alias.py`** entirely:

```python
import httpx
import pytest
import respx

from app.connectors.opnsense.client import ApiError, OpnsenseClient


def _client():
    return OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)


async def test_apply_alias_dry_run_does_no_http():
    out = await _client().apply_alias("set", {"name": "myalias"}, dry_run=True)
    assert out["dry_run"] is True and out["operation"] == "set"


@respx.mock
async def test_apply_alias_add():
    add = respx.post(url__regex=r".*/api/firewall/alias/addItem.*").mock(
        return_value=httpx.Response(200, json={"result": "saved", "uuid": "u1"}))
    rec = respx.post(url__regex=r".*/api/firewall/alias/reconfigure.*").mock(
        return_value=httpx.Response(200, json={"status": "ok"}))
    out = await _client().apply_alias(
        "add", {"name": "a", "type": "host", "content": "1.2.3.4"}, dry_run=False)
    assert out["dry_run"] is False and add.called and rec.called
    assert out["result"]["uuid"] == "u1"


@respx.mock
async def test_apply_alias_set_resolves_uuid_then_setitem():
    search = respx.post(url__regex=r".*/api/firewall/alias/searchItem.*").mock(
        return_value=httpx.Response(200, json={"rows": [{"uuid": "u9", "name": "myalias"}]}))
    setroute = respx.post(url__regex=r".*/api/firewall/alias/setItem/u9.*").mock(
        return_value=httpx.Response(200, json={"result": "saved"}))
    rec = respx.post(url__regex=r".*/api/firewall/alias/reconfigure.*").mock(
        return_value=httpx.Response(200, json={"status": "ok"}))
    out = await _client().apply_alias("set", {"name": "myalias", "content": "5.6.7.8"}, dry_run=False)
    assert search.called and setroute.called and rec.called and out["dry_run"] is False


@respx.mock
async def test_apply_alias_delete_resolves_uuid_then_delitem():
    respx.post(url__regex=r".*/api/firewall/alias/searchItem.*").mock(
        return_value=httpx.Response(200, json={"rows": [{"uuid": "u9", "name": "myalias"}]}))
    delroute = respx.post(url__regex=r".*/api/firewall/alias/delItem/u9.*").mock(
        return_value=httpx.Response(200, json={"result": "deleted"}))
    respx.post(url__regex=r".*/api/firewall/alias/reconfigure.*").mock(
        return_value=httpx.Response(200, json={"status": "ok"}))
    out = await _client().apply_alias("delete", {"name": "myalias"}, dry_run=False)
    assert delroute.called and out["dry_run"] is False


@respx.mock
async def test_apply_alias_set_no_exact_match_raises_and_no_mutation():
    # searchItem returns a substring match only (not exact) -> ApiError, no setItem call.
    respx.post(url__regex=r".*/api/firewall/alias/searchItem.*").mock(
        return_value=httpx.Response(200, json={"rows": [{"uuid": "u1", "name": "myalias_other"}]}))
    setroute = respx.post(url__regex=r".*/api/firewall/alias/setItem.*").mock(
        return_value=httpx.Response(200, json={"result": "saved"}))
    with pytest.raises(ApiError):
        await _client().apply_alias("set", {"name": "myalias"}, dry_run=False)
    assert not setroute.called


@respx.mock
async def test_apply_alias_set_multiple_exact_matches_raises():
    respx.post(url__regex=r".*/api/firewall/alias/searchItem.*").mock(
        return_value=httpx.Response(200, json={"rows": [
            {"uuid": "u1", "name": "myalias"}, {"uuid": "u2", "name": "myalias"}]}))
    with pytest.raises(ApiError):
        await _client().apply_alias("delete", {"name": "myalias"}, dry_run=False)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/l0rdg3x/coding/OPNGMS/backend && .venv/bin/python -m pytest tests/test_connector_apply_alias.py -q`
Expected: FAIL (the new set/delete tests fail because the current code doesn't call searchItem / setItem-with-uuid).

- [ ] **Step 3: Add the `timeout` override to `_request` and `_post`** — in `backend/app/connectors/opnsense/client.py`:

Change the `_request` signature from `async def _request(self, path: str, method: str = "GET", json: dict | None = None) -> httpx.Response:` to:
```python
    async def _request(
        self, path: str, method: str = "GET", json: dict | None = None, timeout: float | None = None
    ) -> httpx.Response:
```
and in the `httpx.AsyncClient(...)` construction inside `_request`, change `timeout=self._timeout` to `timeout=timeout or self._timeout`.

Change `_post` to forward the timeout:
```python
    async def _post(self, path: str, json: dict, timeout: float | None = None) -> dict:
        return self._decode(await self._request(path, "POST", json, timeout=timeout))
```

- [ ] **Step 4: Rewrite `apply_alias` + add `_resolve_alias_uuid`** — replace the existing `apply_alias` method body in `client.py`. First add a module-level constant near the top (after the imports / other constants):
```python
# firewall/alias/reconfigure reloads the firewall tables and is slow; give it room.
RECONFIGURE_TIMEOUT = 120.0
```
Then the method:
```python
    async def apply_alias(self, operation: str, payload: dict, *, dry_run: bool = True) -> dict:
        """Apply a firewall alias change. dry_run=True (default) performs NO mutation.

        Verified against OPNsense 26.1.9: add -> addItem; set/delete need the uuid in the path,
        resolved by exact name via searchItem; reconfigure is slow (long timeout). Goes through
        the single SSRF-guarded HTTP boundary.
        """
        if dry_run:
            return {"dry_run": True, "operation": operation, "target": payload.get("name", "")}
        if operation == "add":
            res = await self._post("firewall/alias/addItem", {"alias": payload})
        elif operation in ("set", "delete"):
            alias_uuid = await self._resolve_alias_uuid(payload.get("name", ""))
            if operation == "set":
                res = await self._post(f"firewall/alias/setItem/{alias_uuid}", {"alias": payload})
            else:
                res = await self._post(f"firewall/alias/delItem/{alias_uuid}", {})
        else:
            raise ApiError(0, f"unknown alias operation: {operation}")
        await self._post("firewall/alias/reconfigure", {}, timeout=RECONFIGURE_TIMEOUT)
        return {"dry_run": False, "result": res}

    async def _resolve_alias_uuid(self, name: str) -> str:
        """Resolve a firewall alias name to its uuid via searchItem (EXACT name match).

        searchItem does substring matching, so we filter to an exact name. Refuses (ApiError)
        when the name is empty or does not resolve to exactly one alias — never mutates on doubt.
        """
        if not name:
            raise ApiError(0, "alias name required for set/delete")
        data = await self._post(
            "firewall/alias/searchItem", {"current": 1, "rowCount": 1000, "searchPhrase": name}
        )
        matches = [r for r in data.get("rows", []) if r.get("name") == name]
        if len(matches) != 1:
            raise ApiError(0, f"alias '{name}' not uniquely resolvable ({len(matches)} exact matches)")
        return matches[0]["uuid"]
```

- [ ] **Step 5: Run the connector tests**

Run: `cd /home/l0rdg3x/coding/OPNGMS/backend && .venv/bin/python -m pytest tests/test_connector_apply_alias.py tests/test_connector_resolver.py -q`
Expected: ALL PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/connectors/opnsense/client.py backend/tests/test_connector_apply_alias.py
git commit -m "fix(opnsense): apply_alias against verified write API (uuid via searchItem, long reconfigure)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `pre_apply_snapshot_id` column + migration 0017

**Files:** Modify `backend/app/models/config_change.py`; Create `backend/migrations/versions/0017_config_change_pre_apply_snapshot.py`, `backend/tests/test_migration_0017.py`.

- [ ] **Step 1: Add the model column** — in `backend/app/models/config_change.py`, after the `result` column, add:
```python
    pre_apply_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), default=None
    )
```
(`uuid` and `UUID` are already imported in that file.)

- [ ] **Step 2: Find the current Alembic head** — read the latest revision id:

Run: `cd /home/l0rdg3x/coding/OPNGMS/backend && grep -E "^revision" migrations/versions/0016_device_edition.py`
Expected: `revision = "0016"`. Use that as `down_revision`.

- [ ] **Step 3: Create `backend/migrations/versions/0017_config_change_pre_apply_snapshot.py`:**
```python
"""config_changes: pre_apply_snapshot_id

Revision ID: 0017
Revises: 0016
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "config_changes",
        sa.Column("pre_apply_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("config_changes", "pre_apply_snapshot_id")
```

- [ ] **Step 4: Create `backend/tests/test_migration_0017.py`:**
```python
from sqlalchemy import text


async def test_pre_apply_snapshot_column_exists(db_engine):
    async with db_engine.connect() as conn:
        cols = (await conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='config_changes'"
        ))).scalars().all()
    assert "pre_apply_snapshot_id" in cols
```

- [ ] **Step 5: Run the migration test + confirm alembic applies cleanly**

Run:
```bash
cd /home/l0rdg3x/coding/OPNGMS/backend
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest tests/test_migration_0017.py -q
docker compose exec -T db dropdb -U opngms --if-exists opngms_migcheck
docker compose exec -T db createdb -U opngms opngms_migcheck
ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_migcheck DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_migcheck SESSION_SECRET=x MASTER_KEY="$(.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" .venv/bin/python -m alembic upgrade head
```
Expected: migration test PASS; alembic ends at revision 0017 with no error.

- [ ] **Step 6: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/models/config_change.py backend/migrations/versions/0017_config_change_pre_apply_snapshot.py backend/tests/test_migration_0017.py
git commit -m "feat(config-push): config_changes.pre_apply_snapshot_id column (migration 0017)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Master switch + pipeline flip + pre-apply backup

**Files:** Modify `backend/app/core/config.py`, `backend/app/services/config_push.py`; extend `backend/tests/test_config_push_apply.py`.

- [ ] **Step 1: Add the master switch** — in `backend/app/core/config.py`, inside the `Settings` class (next to the other scalar settings), add:
```python
    live_push_enabled: bool = False  # master switch: real config push (default OFF -> dry-run)
```

- [ ] **Step 2: Write the failing switch-ON test** — append to `backend/tests/test_config_push_apply.py`:
```python
async def test_apply_live_applies_real_and_snapshots(db_engine, two_tenants, monkeypatch):
    from types import SimpleNamespace

    from app.models.config_snapshot import ConfigSnapshot
    from app.services.config_diff import canonical_hash

    monkeypatch.setattr(
        "app.services.config_push.get_settings",
        lambda: SimpleNamespace(live_push_enabled=True),
    )
    tenant_a, _ = two_tenants
    cid = await _scheduled_change(db_engine, tenant_a, baseline_hash=canonical_hash(XML))
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ch = await s.get(ConfigChange, cid)
        client = FakeClient(XML)
        status = await apply_change(s, ch, client, now=datetime.now(timezone.utc))
        await s.commit()
    assert status == "applied"
    assert client.apply_called is True
    async with factory() as s:
        ch = await s.get(ConfigChange, cid)
        assert ch.result.get("dry_run") is False          # real apply
        assert ch.pre_apply_snapshot_id is not None        # rollback point captured
        snap = await s.get(ConfigSnapshot, ch.pre_apply_snapshot_id)
        assert snap is not None and snap.device_id == ch.device_id
```

- [ ] **Step 3: Run to verify failure**

Run: `cd /home/l0rdg3x/coding/OPNGMS/backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest tests/test_config_push_apply.py -q`
Expected: FAIL on `test_apply_live_...` (AttributeError: module has no `get_settings`, or `dry_run` is True / `pre_apply_snapshot_id` is None).

- [ ] **Step 4: Implement the flip** — in `backend/app/services/config_push.py`:

(a) Add imports at the top (after the existing imports):
```python
import gzip

from app.core import crypto
from app.core.config import get_settings
from app.models.config_snapshot import ConfigSnapshot
```
(`canonical_hash` is already imported; `uuid`, `datetime`, `text`, `AsyncSession` are already imported.)

(b) Add the helper (e.g. after `_advisory_key`):
```python
async def _save_pre_apply_snapshot(session: AsyncSession, change: ConfigChange, xml: str) -> uuid.UUID:
    """Persist the current device config as an encrypted snapshot (a pre-apply rollback point)."""
    snap = ConfigSnapshot(
        tenant_id=change.tenant_id,
        device_id=change.device_id,
        canonical_hash=canonical_hash(xml),
        content_enc=crypto.encrypt_bytes(gzip.compress(xml.encode("utf-8"))),
        opnsense_version="",
        size_bytes=len(xml.encode("utf-8")),
    )
    session.add(snap)
    await session.flush()
    return snap.id
```

(c) Replace the apply tail of `apply_change` (the part from `change.status = "applying"` to `return change.status`) with:
```python
    change.status = "applying"
    await session.flush()
    live = get_settings().live_push_enabled
    try:
        if live:
            # rollback point: persist the pre-apply config (the `xml` already read above).
            change.pre_apply_snapshot_id = await _save_pre_apply_snapshot(session, change, xml)
        res = await client.apply_alias(change.operation, change.payload, dry_run=not live)
        change.status = "applied"
        change.applied_at = now
        change.result = res
    except OpnsenseError:
        change.status = "failed"
        change.result = {"error": "apply failed"}
    await session.flush()
    return change.status
```
(The `xml` variable from the staleness-guard block above is in scope here — reuse it, do NOT re-fetch.)

- [ ] **Step 5: Run the config-push tests (switch-ON new + the 3 existing switch-OFF)**

Run: `cd /home/l0rdg3x/coding/OPNGMS/backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest tests/test_config_push_apply.py -q`
Expected: ALL PASS. (The 3 existing tests run with the real `get_settings()` → `live_push_enabled` default False → `dry_run=True`, unchanged behavior; the new test monkeypatches the switch ON.)

- [ ] **Step 6: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/core/config.py backend/app/services/config_push.py backend/tests/test_config_push_apply.py
git commit -m "feat(config-push): LIVE_PUSH_ENABLED master switch + pre-apply snapshot

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Live end-to-end verify script

**Files:** Create `scripts/verify_live_push.py`.

**Context:** A developer tool (NOT in CI) that exercises the corrected `apply_alias` against real hardware with a throwaway alias and guaranteed cleanup. The implementer CREATES + commits the script; the orchestrator runs it against the box.

- [ ] **Step 1: Create `scripts/verify_live_push.py`:**
```python
#!/usr/bin/env python3
"""Live end-to-end check of apply_alias against real OPNsense hardware (NOT run in CI).

Usage:
    OPNSENSE_URL=https://192.168.1.82 OPNSENSE_KEYFILE=~/path/apikey.txt \
    python scripts/verify_live_push.py

Creates a throwaway host alias via the real apply_alias (add), confirms it exists via
searchItem, then deletes it (apply_alias delete) and confirms it is gone. Credentials are
never printed. The alias is named distinctively and always cleaned up in a finally block.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.connectors.opnsense.client import OpnsenseClient  # noqa: E402

NAME = "opngms_live_push_probe"


def _read_creds(keyfile: str) -> tuple[str, str]:
    key = secret = ""
    for line in Path(keyfile).expanduser().read_text().splitlines():
        if line.startswith("key="):
            key = line[4:].strip()
        elif line.startswith("secret="):
            secret = line[7:].strip()
    if not key or not secret:
        raise SystemExit("key/secret not found in key file")
    return key, secret


async def _present(client) -> int:
    data = await client._post(
        "firewall/alias/searchItem", {"current": 1, "rowCount": 1000, "searchPhrase": NAME}
    )
    return len([r for r in data.get("rows", []) if r.get("name") == NAME])


async def main() -> int:
    base = os.environ["OPNSENSE_URL"]
    key, secret = _read_creds(os.environ["OPNSENSE_KEYFILE"])
    client = OpnsenseClient(base, key, secret, verify_tls=False)
    rc = 1
    try:
        add = await client.apply_alias(
            "add",
            {"enabled": "1", "name": NAME, "type": "host", "content": "192.0.2.50",
             "description": "OPNGMS live-push probe (delete me)"},
            dry_run=False,
        )
        print(f"add        -> {add.get('result')}")
        print(f"present    -> {await _present(client) == 1}")
        rc = 0
    finally:
        try:
            if await _present(client):
                await client.apply_alias("delete", {"name": NAME}, dry_run=False)
            gone = await _present(client) == 0
            print(f"cleanup    -> gone={gone}")
            if not gone:
                rc = 1
        except Exception as exc:  # noqa: BLE001
            print(f"CLEANUP ERROR: {type(exc).__name__}: {exc}")
            rc = 1
    print("ALL PASS" if rc == 0 else "FAILED")
    return rc


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

- [ ] **Step 2: Verify the script imports cleanly** (no DB / no network needed for the import):

Run: `cd /home/l0rdg3x/coding/OPNGMS && backend/.venv/bin/python -c "import ast; ast.parse(open('scripts/verify_live_push.py').read()); print('parse OK')"`
Expected: `parse OK`.

- [ ] **Step 3: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add scripts/verify_live_push.py
git commit -m "tools(config-push): live apply_alias verify script (throwaway alias, cleanup)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

> **Orchestrator note:** after this task, the orchestrator runs the script against the real box (`OPNSENSE_URL=https://192.168.1.82 OPNSENSE_KEYFILE=/home/l0rdg3x/Scaricati/OPNsense.internal_root_apikey.txt`) and confirms `add -> saved`, `present -> True`, `cleanup -> gone=True`, `ALL PASS`. This exercises the corrected add (addItem+reconfigure) and delete (searchItem→uuid→delItem+reconfigure) paths on hardware.

---

## Final verification

- [ ] Full backend suite green: `cd /home/l0rdg3x/coding/OPNGMS/backend && TEST_DATABASE_URL=... ADMIN_DATABASE_URL=... .venv/bin/python -m pytest -q`
- [ ] Live e2e (orchestrator): `scripts/verify_live_push.py` against the box → `ALL PASS`, box left clean
- [ ] Dispatch a final holistic review, then superpowers:finishing-a-development-branch.

---

## Self-Review (author)

**Spec coverage:** verified write API + apply_alias fix incl. uuid-via-searchItem exact-match and long reconfigure timeout (Task 1); `pre_apply_snapshot_id` + migration (Task 2); `LIVE_PUSH_ENABLED` master switch + pipeline flip + pre-apply snapshot (Task 3); live e2e script (Task 4). Out-of-scope items (auto-rollback, other kinds, runtime toggle) are not implemented, as specified.

**Placeholder scan:** every code step is complete; commands have expected output; the orchestrator live-run note is concrete (exact env + expected lines), not a TODO.

**Type consistency:** `apply_alias(operation, payload, *, dry_run)` and `_resolve_alias_uuid(name)`, `RECONFIGURE_TIMEOUT`, the `_request`/`_post` `timeout` param (Task 1); `pre_apply_snapshot_id` column (Task 2) used by `_save_pre_apply_snapshot` + `apply_change` (Task 3); `get_settings().live_push_enabled` consistent; the switch-ON test monkeypatches `app.services.config_push.get_settings`. `ConfigSnapshot(tenant_id, device_id, canonical_hash, content_enc, opnsense_version, size_bytes)` matches the model.
