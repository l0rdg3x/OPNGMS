# OPNsense Live Config Push (4D-b / 4D-d) — Design Spec

**Date:** 2026-06-11
**Status:** Approved (design)
**Builds on:** the existing dry-run config-push pipeline (`config_push.py`: draft→scheduled→applying→applied/failed/conflict, per-device advisory lock, staleness guard, secret-safe preview, `apply_config_change` worker). That pipeline already exists and is safe; today `apply_change` always calls `apply_alias(..., dry_run=True)` so nothing is ever written.

## Goal

Turn the dry-run config-push pipeline into a **real** one for firewall **aliases**: fix `apply_alias` against the verified OPNsense write API, and let the pipeline actually mutate a device — gated behind a default-OFF master switch, with a config backup captured before every real apply.

## Architecture

`apply_alias` is corrected to the firewall/alias write API verified live on OPNsense 26.1.9 (uuid-in-path for set/delete, name→uuid via searchItem, slow `reconfigure` with a long timeout). A global `LIVE_PUSH_ENABLED` setting (default `False`) controls whether `apply_change` applies for real (`dry_run = not LIVE_PUSH_ENABLED`); when real, it persists the current config as a pre-apply snapshot (rollback point) before mutating. All existing safety (advisory lock, staleness guard, states, preview) is unchanged.

## Tech Stack

Python 3.14, the SSRF-guarded connector boundary, SQLAlchemy/Alembic (one new column), pydantic-settings (the switch), pytest + respx (unit) + a live e2e script.

---

## 1. Verified firewall/alias write API (live, OPNsense 26.1.9)

Captured against the real box with a throwaway alias (created + deleted, box restored):

| Op | Request | Response |
|---|---|---|
| create | `POST firewall/alias/addItem` body `{"alias": {...}}` | `{"result":"saved","uuid":"<uuid>"}` |
| update | `POST firewall/alias/setItem/<uuid>` body `{"alias": {...}}` | `{"result":"saved"}` |
| delete | `POST firewall/alias/delItem/<uuid>` | `{"result":"deleted"}` |
| lookup | `POST firewall/alias/searchItem` body `{"current":1,"rowCount":N,"searchPhrase":"<name>"}` | `{"rows":[{"uuid","name",...}]}` (substring match) |
| apply | `POST firewall/alias/reconfigure` | `{"status":"ok"}` — **slow** (timed out at 25s, succeeded at 60s) |

Confirmed bug in the current `apply_alias`: `setItem`/`delItem` are called WITHOUT the uuid in the path, and `reconfigure` runs under the default 10s timeout (would time out). An alias is created with `{"alias": {"enabled":"1","name":...,"type":"host","content":...,"description":...}}`.

## 2. Fix `apply_alias` (connector)

`apply_alias(operation, payload, *, dry_run=True)`:
- `dry_run=True` → unchanged (returns the secret-safe summary, no mutation).
- `add` → `POST firewall/alias/addItem` `{"alias": payload}` → returns `{"result","uuid"}`.
- `set` / `delete` → resolve the uuid by **exact name**: `POST firewall/alias/searchItem {"current":1,"rowCount":1000,"searchPhrase": payload["name"]}`, then keep rows where `row["name"] == payload["name"]`. If exactly one match → use its uuid; if **0 or >1** exact matches → raise `ApiError` (refuse the ambiguous/missing apply, no mutation). Then `POST firewall/alias/setItem/<uuid>` `{"alias": payload}` or `POST firewall/alias/delItem/<uuid>`.
- After any add/set/delete → `POST firewall/alias/reconfigure` with a **long timeout** (`RECONFIGURE_TIMEOUT = 120.0`s). The add/set/delete/searchItem calls themselves are fast and use the default timeout; only `reconfigure` (which reloads the firewall tables) needs the long timeout.
- Returns `{"dry_run": False, "result": <op result>}`.

**Timeout override:** add an optional `timeout: float | None = None` to `_request` (and `_post`) that overrides `self._timeout` for that single call (used only by the `reconfigure` POST). The httpx client is already constructed per-request, so it is a one-line `timeout=timeout or self._timeout`.

## 3. Master switch — `LIVE_PUSH_ENABLED`

A global setting in `app/core/config.py`: `live_push_enabled: bool = False` (env var `LIVE_PUSH_ENABLED`). The default is OFF — the dry-run pipeline stays the default until an operator deliberately enables real push at deploy/ops level. (A runtime/UI toggle is out of scope for v1.)

## 4. Pre-apply backup (rollback point)

`config_changes` gains a nullable `pre_apply_snapshot_id` (UUID) — migration **0017**. In `apply_change`, when applying for real, **after** the staleness guard passes and **before** `apply_alias`, persist the config XML already fetched for the staleness check as a new encrypted `ConfigSnapshot` (reuse the existing encryption path: `crypto.encrypt_bytes(gzip.compress(xml.encode()))`, same fields as `config_backup.backup_config`), and set `change.pre_apply_snapshot_id = snapshot.id`. This guarantees a restore point linked to the change. (Automatic rollback — re-uploading the snapshot via `core/backup/restore` — is a documented TODO, not built in v1.)

## 5. Pipeline flip — `apply_change`

Unchanged: `status != "scheduled"` early-return, advisory lock, staleness guard (re-read config, compare `canonical_hash`, → `conflict` on drift). Changed tail:

```python
    change.status = "applying"
    await session.flush()
    live = get_settings().live_push_enabled
    try:
        if live:
            # rollback point: persist the pre-apply config (already fetched as `xml`)
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

`_save_pre_apply_snapshot` is a small helper in `config_push.py` that builds + flushes a `ConfigSnapshot` from the given xml and returns its id. With the switch OFF, `dry_run=True` and no snapshot is taken (behavior identical to today).

## 6. Error handling

- `reconfigure`/write timeout or HTTP error → `OpnsenseError` → `change.status="failed"` (the staleness guard prevents any clobber on the next attempt; the device config is whatever the partial op left — for aliases, add/set/delete are single atomic API calls, so partial state is just "applied or not").
- set/delete name resolves to 0 or >1 exact matches → `ApiError` (caught as `OpnsenseError`) → `failed` with no mutation.
- Switch OFF → dry-run, no mutation, `applied` with the dry-run summary (today's behavior).
- The advisory lock + staleness guard remain the concurrency/no-clobber guarantees.

## 7. Testing

- **Connector (respx)** — `apply_alias`:
  - `add` → addItem called, returns uuid.
  - `set`/`delete` → searchItem called, exact-name match picks the uuid, setItem/`<uuid>` or delItem/`<uuid>` called, then reconfigure.
  - exact-name edge: 0 matches → ApiError; >1 exact matches → ApiError; 1 substring-but-not-exact → ApiError (no mutation).
  - `dry_run=True` → no HTTP, returns summary.
  - reconfigure uses the long timeout (assert the call is made; respx doesn't enforce timeout, but assert apply ordering).
- **Pipeline** — `apply_change`:
  - switch OFF (default) → `apply_alias` called with `dry_run=True`, no snapshot, status `applied` (existing tests stay green).
  - switch ON → `dry_run=False`, `pre_apply_snapshot_id` set, a new `ConfigSnapshot` row exists, status `applied`.
  - conflict (drift) and lock-not-acquired paths unchanged.
- **Live e2e** (`scripts/verify_live_push.py`, not in CI; test box, `LIVE_PUSH_ENABLED=1`): create a draft "add throwaway alias" change → schedule → run `apply_change` against the real box → verify the alias exists via `searchItem` → **cleanup** (delItem/`<uuid>` + reconfigure). Confirms the whole pipeline end-to-end on hardware.

## 8. Out of scope (TODO)

- **Automatic rollback** — re-uploading the `pre_apply_snapshot` via `core/backup/restore` on a bad apply. v1 only captures the backup; restore is manual/explicit. (TODO)
- Config writes for **kinds other than `alias`** (the pipeline's `kind` field is generic; new kinds are added later as data + a connector method).
- **Runtime/UI toggle** of the master switch (env-var only for v1).

## 9. File structure

- **Modify:** `app/connectors/opnsense/client.py` (fix `apply_alias`; add `timeout` override to `_request`/`_post`; `RECONFIGURE_TIMEOUT` const), `app/core/config.py` (`live_push_enabled`), `app/services/config_push.py` (`apply_change` flip + `_save_pre_apply_snapshot`), `app/models/config_change.py` (`pre_apply_snapshot_id`).
- **Create:** `backend/migrations/versions/0017_config_change_pre_apply_snapshot.py`, `scripts/verify_live_push.py`, tests (`test_connector_apply_alias.py` extended, `test_config_push_apply.py` extended, a migration test).
- **Unchanged:** the pipeline states, advisory lock, staleness guard, preview, worker task, API endpoints.
