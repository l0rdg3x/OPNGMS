# Per-tenant retention SP-2 (log lake) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-tenant retention for the OpenSearch log lake — the 4th and final retention store — completing the milestone.

**Architecture:** syslog-ng writes per-tenant daily indices (`opngms-logs-<tenant_id>-DATE`); a worker job deletes each tenant's old indices at its effective retention (reusing the SP-1 resolver) and replaces the global ISM. `log_lake` becomes the 4th `RETENTION_STORES` store. The report guard is unaffected (log_lake never bounds a report).

**Tech Stack:** Python 3.14 / FastAPI / SQLAlchemy async / ARQ worker / httpx → OpenSearch / pytest. React 19 / Mantine / 12-locale i18n. syslog-ng + OpenSearch (the `logs` compose overlay).

**Spec:** `docs/superpowers/specs/2026-06-15-retention-per-tenant-sp2-log-lake-design.md`

---

## File Structure

**PR1 — Backend:**
- Modify: `backend/app/services/retention.py` (`RETENTION_STORES += "log_lake"`), `backend/app/services/runtime_settings.py` (knob), `backend/app/services/report_retention.py` (`REPORT_BOUNDING_STORES`), `backend/app/api/system.py` (lowered trigger uses report-bounding stores), `backend/app/cli.py` (stop/ remove ISM), `backend/app/worker.py` (cron), `deploy/syslog-ng/syslog-ng.conf` (per-tenant index URL), `deploy/opensearch/` (+ compose/docs notes).
- Create: `backend/app/services/log_lake_retention.py` (parse + decide + purge), `backend/tests/test_log_lake_retention.py`.

**PR2 — Frontend:** `frontend/src/retention/RetentionCard.tsx` (+`log_lake` store) + i18n (12 locales) + tests.

**Then:** E2E bring-up verification → release v0.11.0 + CHANGELOG → README/Wiki docs.

---

## PR1 — Backend (log lake retention)

Branch: `feat/retention-loglake` (holds the spec).

### Task 1: `log_lake` as the 4th store + the registry knob

**Files:** Modify `backend/app/services/retention.py`, `backend/app/services/runtime_settings.py`; Test `backend/tests/test_runtime_settings.py`.

- [ ] **Step 1: Write the failing test.**
```python
async def test_log_lake_retention_knob():
    from app.services.runtime_settings import runtime_defaults, _BY_KEY
    from app.services.retention import RETENTION_STORES
    assert "log_lake" in RETENTION_STORES
    assert runtime_defaults()["log_lake_retention_days"] == 30
    assert _BY_KEY["log_lake_retention_days"].active is True
```
- [ ] **Step 2: Run → FAIL.** `cd backend && python -m pytest tests/test_runtime_settings.py::test_log_lake_retention_knob -q`
- [ ] **Step 3: Implement.**
  - `retention.py`: `RETENTION_STORES = ("perimeter", "events", "metrics", "log_lake")`.
  - `runtime_settings.py`: append to `RUNTIME_SETTINGS`:
    ```python
    RuntimeSetting("log_lake_retention_days", int, lambda s: s.log_retention_days, 1, 3650, "retention"),
    ```
    (Bridges the existing `LOG_RETENTION_DAYS` env / `Settings.log_retention_days` default — no rename, back-compat preserved.)
- [ ] **Step 4: Run → PASS.** Also run `tests/test_retention_api.py` (the per-tenant API now round-trips `log_lake` automatically since it validates against `RETENTION_STORES`).
- [ ] **Step 5: Commit** `feat(retention): add log_lake as the 4th retention store + global knob`.

### Task 2: `REPORT_BOUNDING_STORES` — don't enumerate tenants when lowering log_lake

**Files:** Modify `backend/app/services/report_retention.py`, `backend/app/api/system.py`; Test `backend/tests/test_retention_global_impacts.py`.

- [ ] **Step 1: Write the failing test** (lowering `log_lake_retention_days` must NOT enumerate tenants → empty impacts, even with a monthly schedule that would over-run a tiny value):
```python
async def test_lowering_log_lake_yields_no_impacts(api_client, db_engine):
    await _superadmin(api_client)
    tid = await _seed_tenant(db_engine, "imp-loglake")
    await _ensure_settings(db_engine, tid)
    await _add_schedule(db_engine, tid, frequency="monthly")
    # log_lake is NOT a report-bounding store → lowering it never impacts a report.
    assert await _put_settings(api_client, {"log_lake_retention_days": 1}) == []
```
- [ ] **Step 2: Run → FAIL** (today `lowered` iterates `RETENTION_STORES`, so log_lake would trigger the scan; the scan finds no `limiting_store=="log_lake"` warning so it returns `[]` anyway — the test may already pass. If it passes, still do Step 3 to remove the pointless enumeration + assert it's skipped, e.g. by spying that no tenant query runs, or accept the behavioural test as sufficient.)
- [ ] **Step 3: Implement.** In `report_retention.py` add `REPORT_BOUNDING_STORES = ("perimeter", "events", "metrics")` (the stores that can bound a report — i.e. the keys present in `SECTION_STORES`). In `system.py`, change the `lowered` comprehension to iterate `REPORT_BOUNDING_STORES` instead of `RETENTION_STORES`, so a log_lake-only lowering never enumerates tenants.
- [ ] **Step 4: Run → PASS** (the new test + the existing global-impacts tests).
- [ ] **Step 5: Commit** `feat(retention): scope the impacted-tenants scan to report-bounding stores`.

### Task 3: log-lake index parsing + delete decision (pure logic, TDD)

**Files:** Create `backend/app/services/log_lake_retention.py`; Test `backend/tests/test_log_lake_retention.py`.

- [ ] **Step 1: Write the failing tests.**
```python
from datetime import date
from app.services.log_lake_retention import parse_index, indices_to_delete

def test_parse_index():
    assert parse_index("opngms-logs-3f...uuid...-2026.06.10") == ("3f...uuid...", date(2026, 6, 10))
    assert parse_index("opngms-logs-2026.06.10") == (None, date(2026, 6, 10))   # legacy date-only
    assert parse_index("opngms-logs-weird") is None
    assert parse_index("other-index") is None

def test_indices_to_delete():
    today = date(2026, 6, 20)
    names = [
        "opngms-logs-aaaa-2026.06.01",  # tenant aaaa, 19d old
        "opngms-logs-aaaa-2026.06.19",  # tenant aaaa, 1d old (kept)
        "opngms-logs-bbbb-2026.06.01",  # tenant bbbb, 19d old
        "opngms-logs-2026.05.01",       # legacy, 50d old
    ]
    overrides = {"aaaa": {"log_lake": 7}}  # aaaa keeps 7d; bbbb + legacy use global 30
    to_del = indices_to_delete(names, today, global_default=30, overrides_by_tenant=overrides)
    # aaaa@7d: 19d>7 -> delete the 06.01; 1d kept. bbbb@30d: 19d<30 kept. legacy@30d: 50d>30 -> delete.
    assert set(to_del) == {"opngms-logs-aaaa-2026.06.01", "opngms-logs-2026.05.01"}
```
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement the pure helpers** in `log_lake_retention.py`:
```python
import re
import uuid
from datetime import date

from app.services.retention import effective_retention_days

_RE = re.compile(r"^opngms-logs-(?:(?P<tid>[0-9a-fA-F-]{36})-)?(?P<y>\d{4})\.(?P<m>\d{2})\.(?P<d>\d{2})$")


def parse_index(name: str) -> tuple[str | None, date] | None:
    """(tenant_id|None, date) for an opngms-logs index name, else None. tenant_id None = legacy date-only."""
    m = _RE.match(name)
    if not m:
        return None
    tid = m.group("tid")
    if tid is not None:
        try:
            uuid.UUID(tid)
        except ValueError:
            return None
    return tid, date(int(m.group("y")), int(m.group("m")), int(m.group("d")))


def indices_to_delete(index_names, today: date, *, global_default: int,
                      overrides_by_tenant: dict[str, dict]) -> list[str]:
    """Indices whose date is older than their tenant's effective log_lake retention (legacy → global)."""
    out: list[str] = []
    for name in index_names:
        parsed = parse_index(name)
        if parsed is None:
            continue  # not ours
        tid, idx_date = parsed
        override = overrides_by_tenant.get(tid) if tid else None
        days = effective_retention_days("log_lake", global_default=global_default, tenant_override=override)
        if (today - idx_date).days > days:
            out.append(name)
    return out
```
- [ ] **Step 4: Run → PASS.** **Step 5: Commit** `feat(retention): log-lake index parsing + per-tenant delete decision`.

### Task 4: The OpenSearch purge function + worker cron

**Files:** Modify `backend/app/services/log_lake_retention.py`, `backend/app/worker.py`; Test `backend/tests/test_log_lake_retention.py`.

- [ ] **Step 1: Write the failing test** (mock OpenSearch HTTP — `respx` if available, else monkeypatch `httpx.AsyncClient`): `_cat/indices` returns a known list; assert the job issues `DELETE` for exactly the over-age indices and **no-ops** when `opensearch_url` is falsy. Read the global config + a tenant override from the DB (owner session via `db_engine`).
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** `async def purge_log_lake(session, today, *, opensearch_url) -> int | str`:
  - if not `opensearch_url`: return `"skipped"`.
  - `cfg = await get_runtime_config(session)`; `global_default = int(cfg["log_lake_retention_days"])`.
  - `overrides_by_tenant = {str(tid): ov for tid, ov in (await session.execute(select(TenantRetention.tenant_id, TenantRetention.overrides))).all()}` (owner session — sees all rows).
  - `GET {opensearch_url}/_cat/indices/opngms-logs-*?format=json&h=index` via `httpx.AsyncClient(timeout=15.0)` (mirror `log_fleet.py`); on `httpx.HTTPError` → log warning, return `"unreachable"`.
  - `victims = indices_to_delete([r["index"] for r in resp.json()], today, global_default=global_default, overrides_by_tenant=overrides_by_tenant)`.
  - `DELETE {opensearch_url}/<index>` each (best-effort, count). Return the count.
  - Worker cron `purge_log_lake_job(ctx)` in `worker.py` (mirror `purge_timeseries_retention`): owner session, `await purge_log_lake(session, datetime.now(UTC).date(), opensearch_url=get_settings().opensearch_url)`; register in `WorkerSettings.functions` + a daily `cron_jobs` slot (e.g. 05:00). No DB commit needed (read-only on the DB).
- [ ] **Step 4: Run → PASS.** **Step 5: Commit** `feat(retention): per-tenant log-lake purge worker job`.

### Task 5: syslog-ng per-tenant index + remove the global ISM

**Files:** Modify `deploy/syslog-ng/syslog-ng.conf`, `backend/app/cli.py`, `deploy/opensearch/` (+ compose/docs notes).

- [ ] **Step 1: syslog-ng** — change the `d_opensearch` destination URL to
  `url("`OPENSEARCH_URL`/opngms-logs-${tenant_id}-${YEAR}.${MONTH}.${DAY}/_doc")`. (The `f_has_tenant` filter
  already guarantees `tenant_id` is non-empty.)
- [ ] **Step 2: cli.py** — stop applying the `opngms-logs-retention` ISM policy; instead **remove** it so it
  can't keep deleting per-tenant indices at the global age: keep the index-template PUT, then
  `DELETE {opensearch_url}/_plugins/_ism/policies/opngms-logs-retention` (ignore 404) and best-effort
  `POST {opensearch_url}/_plugins/_ism/remove/opngms-logs-*` to detach from existing indices. Update the
  printed message to say the worker now owns log retention. (Exact calls confirmed at the bring-up.)
- [ ] **Step 3:** retire `deploy/opensearch/ism-policy.json`; add a note in `docker-compose.logs*.yml` / the
  deploy README that the `purge_log_lake` worker job owns log-lake retention (per-tenant).
- [ ] **Step 4:** `ruff check app/` clean; full backend suite green (`python -m pytest -q`, single process) — report the count.
- [ ] **Step 5: Commit** `feat(retention): syslog-ng per-tenant indices + retire the global ISM`.

### Task 6: PR1 — open, green CI, squash-merge
- [ ] Push, open PR "feat(retention): per-tenant log-lake retention (SP-2 PR1)", green CI, squash-merge.

---

## PR2 — Frontend (structured outline)

Branch from updated `main`: `feat/retention-loglake-ui`.

- **`gen:api`** (the `log_lake` default now flows through `GET /retention`'s typed `defaults`/`overrides`).
- **Retention card:** add `"log_lake"` to the hardcoded `STORES` in `frontend/src/retention/RetentionCard.tsx` so the 4th NumberInput renders (inherit/override/clear, same as the others).
- **i18n (12 locales):** `system.runtime.items.log_lake_retention_days` ({label, help}) for the global group + `retention.stores.log_lake` for the card. Real translations.
- The global Runtime-settings group auto-renders the knob (already active from PR1). The per-tenant warnings + impacts UI are unaffected (log_lake never warns/impacts).
- **Tests:** the card renders 4 inputs incl. log_lake; saves/clears a log_lake override. Gate: `npm run build` + `npm test` + `npm run lint`.
- Open PR "feat(retention): log-lake retention in the UI (SP-2 PR2)". Merge.

---

## E2E bring-up verification (REQUIRED before release — user: "bisogna testarlo")

Do this locally with the `logs` compose overlay; fix anything real OpenSearch/syslog-ng surfaces, then
back-port fixes into PR1.

- [ ] Bring up the log lake: `docker compose -f docker-compose.prod.yml -f docker-compose.logs.yml up -d opensearch syslog-ng` (+ the app/worker as needed). Provision a device cert (the syslog provisioning flow) so syslog-ng has a client cert whose **O = a real tenant_id**.
- [ ] Send a test log through syslog-ng (or enable forwarding on a seeded device); verify it lands in
  `opngms-logs-<tenant_id>-<today>` (`GET {opensearch_url}/_cat/indices/opngms-logs-*?v`).
- [ ] Verify the **Log fleet** dashboard / search still find it (the `opngms-logs-*` glob).
- [ ] Create an artificially-old per-tenant index (PUT a doc with a back-dated index name) + a legacy
  date-only one; run `purge_log_lake` (trigger the cron or call it directly) and confirm: the over-age
  per-tenant index is deleted at the tenant's retention, a per-tenant **override** is respected (set a long
  override → its old index survives), the legacy index is deleted at the global retention, fresh indices stay.
- [ ] Confirm the ISM removal actually took effect (the old policy no longer auto-deletes per-tenant indices):
  record the exact `_ism/remove` + policy-delete calls/behaviour for this OpenSearch version in the spec/PR.
- [ ] Tear down the bring-up cleanly.

---

## Release + docs

- [ ] **Tag v0.11.0 + CHANGELOG** — cut **v0.11.0** covering **SP-1 + SP-2** (per-tenant retention for all
  four stores + the report↔retention guard). Move `[Unreleased]` → `## [0.11.0]` + compare link; the release
  workflow derives the GitHub Release body from it. (User: release only after SP-2.)
- [ ] **README + Wiki refresh** — the deferred docs pass covering the **Audit viewer (v0.10.0)** AND the
  **Retention** milestone (SP-1 + SP-2). See [[docs-refresh-after-retention-sp2]] / [[keep-readme-updated]].

---

## Self-review notes
- **Spec coverage:** PR1 = the 4th store + knob (spec §4) + the worker job (spec §2) + syslog index (spec §1)
  + ISM removal (spec §3) + the report-guard refinement (spec §5). PR2 = UI (spec §6). Then the required
  bring-up (spec §Testing) + release.
- **Report guard untouched:** `log_lake` is added to `RETENTION_STORES` (valid override key) but NOT to
  `SECTION_STORES`; the impacts scan keys off the new `REPORT_BOUNDING_STORES`, so log_lake never bounds a
  report nor enumerates tenants. Verified end-to-end in the plan.
- **No-op safety:** every OpenSearch touch (worker job, cli.py) degrades gracefully when the log lake isn't
  deployed/reachable.
- **Type consistency:** `RETENTION_STORES` (4), `REPORT_BOUNDING_STORES` (3), `log_lake_retention_days`
  (registry key = `{store}_retention_days`), `parse_index`/`indices_to_delete`/`purge_log_lake` — one name
  each, used identically across tasks.
