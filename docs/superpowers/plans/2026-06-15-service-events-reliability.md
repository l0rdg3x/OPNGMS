# Service Events (Reliability) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Classify reliability events (reboot / service crash-restart / disk-FS warnings) from the OPNsense
system log into the `events` hypertable (`source="service"`), and surface them as a per-device timeline
tab, an Overview card, a report section, and ingest-time alerts.

**Architecture:** Reuse the source-pluggable event pipeline (`app/services/ingest.py`), the keyset events
API (`GET /events`), the alert model, and the report-section registry. The only novel piece is the
curated, fail-safe **classifier** (`parse_service_events`). No schema change / migration.

**Tech Stack:** Python 3.14 / FastAPI / pytest; React 19 / TS / Mantine v9 / Vitest. Spec:
`docs/superpowers/specs/2026-06-15-service-events-reliability-design.md`.

**Execution note:** 3 PRs — PR1 backend ingest+alerts, PR2 frontend (device tab + Overview card + i18n),
PR3 report section. PR1 detailed below; PR2/PR3 are structured outlines to expand at execution time.

---

## PR1 — Backend: classifier + ingest + alerts

> READ first: `app/connectors/opnsense/parsers.py` (`parse_auth_failures` ~294, the helpers `_rows`,
> `parse_ts`, `event_key`, and the regex style), `app/connectors/opnsense/profiles.py` (the CAPABILITY
> map + the `auth_failures` entry ~98 + `_POST`/`_default`/`_spec`/`MAX_QUERY_ROWS`),
> `app/connectors/opnsense/client.py` (`get_auth_failures` ~447 + `_capability`), `app/services/ingest.py`
> (SOURCES, `_fetch`, `_normalize`), `app/services/alerting.py` (`_open_alerts`, `_alert`, dedup), and how
> `ingest_events` + perimeter alerts are invoked in `app/worker.py`.

### Task 1: `parse_service_events` classifier

**Files:**
- Modify: `backend/app/connectors/opnsense/parsers.py`
- Test: `backend/tests/test_parse_service_events.py`

- [ ] **Step 1: Write the failing test** — synthetic system-log rows (the `core/system` row shape:
  `{timestamp, severity, process_name, pid, line}`) for each category + noise, asserting classification:

```python
from app.connectors.opnsense.parsers import parse_service_events

def _row(process, line, severity="notice", ts="2026-06-15T10:00:00"):
    return {"timestamp": ts, "process_name": process, "severity": severity, "pid": "1", "line": line}

def _data(rows):
    return {"rows": rows}

def test_classifies_reboot():
    out = parse_service_events(_data([_row("shutdown", "reboot by root", "notice")]))
    assert len(out) == 1 and out[0]["category"] == "reboot" and out[0]["name"] == "reboot"

def test_classifies_service_crash():
    out = parse_service_events(_data([_row("kernel", "pid 42 (suricata), jid 0, uid 0: exited on signal 11 (core dumped)", "crit")]))
    assert out[0]["category"] == "service" and out[0]["name"] == "service_crashed" and out[0]["severity"] == "high"

def test_classifies_disk_full():
    out = parse_service_events(_data([_row("kernel", "/var: filesystem full", "err")]))
    assert out[0]["category"] == "disk" and out[0]["name"] == "filesystem_full" and out[0]["severity"] == "high"

def test_drops_noise():
    out = parse_service_events(_data([_row("dhcp6c", "advertise contains NoAddrsAvail status", "info")]))
    assert out == []

def test_event_key_is_stable_and_carries_attributes():
    rows = [_row("shutdown", "reboot by root", "notice")]
    a = parse_service_events(_data(rows)); b = parse_service_events(_data(rows))
    assert a[0]["event_key"] == b[0]["event_key"]
    assert a[0]["attributes"]["process"] == "shutdown"
```

- [ ] **Step 2: Run it — confirm it fails** (`parse_service_events` undefined).
  `cd backend && . .venv/bin/activate && python -m pytest tests/test_parse_service_events.py -q`

- [ ] **Step 3: Implement `parse_service_events`** in `parsers.py` (mirror `parse_auth_failures`'s
  fail-safe `_rows` loop + `parse_ts` + `event_key`). A curated, ORDERED rule set; first match wins;
  unmatched rows skipped. **These patterns are a RUNTIME-VERIFY starter set — extensible.**

```python
import hashlib
import re

# (category, name, base_severity, process_predicate, line_regex). process None = any process.
_SERVICE_RULES = [
    ("reboot", "reboot", "high", {"shutdown"}, re.compile(r"\breboot\b", re.I)),
    ("reboot", "boot", "medium", {"syslogd"}, re.compile(r"kernel boot file", re.I)),
    ("service", "service_crashed", "high", None,
        re.compile(r"\bexited on signal\b|\bcore dumped\b|\bterminated abnormally\b", re.I)),
    ("service", "service_restarted", "medium", {"configd.py", "configd"},
        re.compile(r"\brestart(ing|ed)?\b", re.I)),
    ("disk", "filesystem_full", "high", None,
        re.compile(r"No space left on device|filesystem full|out of (disk )?space", re.I)),
    ("disk", "disk_error", "high", {"smartd"}, re.compile(r"\b(error|fail|offline)\b", re.I)),
    ("disk", "pool_degraded", "high", None, re.compile(r"\b(DEGRADED|FAULTED|pool .* unavailable)\b")),
]
_HIGH_LOG_SEV = {"emerg", "alert", "crit", "err", "error"}


def parse_service_events(data) -> list[dict]:
    """system-log rows -> classified reliability events. Fail-safe: unrecognized lines are skipped
    (we store only classified reliability events, not the whole log). RUNTIME-VERIFY rule set."""
    out: list[dict] = []
    for r in _rows(data, "rows"):
        if not isinstance(r, dict):
            continue
        proc = str(r.get("process_name", ""))
        line = str(r.get("line", ""))
        log_sev = str(r.get("severity", "")).lower()
        for category, name, base_sev, procs, rx in _SERVICE_RULES:
            if procs is not None and proc not in procs:
                continue
            if not rx.search(line):
                continue
            ts = parse_ts(r.get("timestamp"))
            severity = "high" if log_sev in _HIGH_LOG_SEV else base_sev
            digest = hashlib.sha1(f"{name}|{line}".encode()).hexdigest()[:16]
            out.append({
                "time": ts,
                "category": category,
                "name": name,
                "severity": severity,
                "event_key": event_key(ts, name, digest),
                "attributes": {"process": proc, "message": line[:500], "log_severity": log_sev},
            })
            break
    return out
```
(Confirm `_rows`, `parse_ts`, `event_key` signatures in the file and match them; `event_key` may take a
different arg count — adapt the call to whatever the existing parsers use for a stable dedup key.)

- [ ] **Step 4: Run — confirm pass.** **Step 5: Lint** `ruff check app/connectors/opnsense/parsers.py`.
  **Step 6: Commit** `feat(reliability): classify service/reliability events from the system log`.

### Task 2: connector capability + `get_service_events` + SOURCES wiring

**Files:**
- Modify: `backend/app/connectors/opnsense/profiles.py`, `backend/app/connectors/opnsense/client.py`,
  `backend/app/services/ingest.py`
- Test: `backend/tests/test_ingest_service.py` (+ extend a connector capability test if one exists)

- [ ] **Step 1: Failing test** — (a) a fake client with `get_service_events` returning classified rows →
  `ingest_events` over `SOURCES` inserts `source="service"` events and advances the `(device,"service")`
  cursor; a second poll with the same rows inserts nothing (dedup). Mirror the existing ingest test
  (`grep -rl "ingest_events\|_ingest_source" backend/tests`).

- [ ] **Step 2: Run — confirm fail.**

- [ ] **Step 3: Implement:**
  - `profiles.py` CAPABILITY map: add
    `"service_events": [_default(_spec(_POST("diagnostics/log/core/system", {"current":1,"rowCount":MAX_QUERY_ROWS,"searchPhrase":""}), combine=lambda r: parsers.parse_service_events(r[0])))]`.
  - `client.py`: `async def get_service_events(self, since=None): return await self._capability("service_events")` (mirror `get_auth_failures`; `since` filtered downstream).
  - `ingest.py`: add `"service"` to `SOURCES`; in `_fetch`, `if source == "service": return await client.get_service_events(since)`.

- [ ] **Step 4: Run — confirm pass.** **Step 5: Commit** `feat(reliability): ingest service events as a new source`.

### Task 3: ingest-time alerts for high-severity service events

**Files:**
- Modify: `backend/app/services/ingest.py` (or a new `app/services/service_alerts.py`),
  `backend/app/worker.py` (wire where perimeter/event alerts run)
- Test: `backend/tests/test_service_alerts.py`

- [ ] **Step 1: Failing test** — ingesting a new high-severity service event (`severity=="high"`, category
  in {reboot, service, disk}) raises exactly one `Alert(type="service_event", label=…)`; a repeat of the
  same event raises none (reuse the open-alert dedup in `alerting.py`); an alert-raise exception doesn't
  abort the ingest (events still committed).

- [ ] **Step 2: Run — confirm fail.**

- [ ] **Step 3: Implement** — have the service-source ingest return (or collect) the NEW high-severity
  events, then a small `raise_service_alerts(session, device, new_high_events)` that opens a deduped
  `Alert(type="service_event", label=f"{name}: {device.name}")` using the `_open_alerts`/`_alert` helpers.
  Call it from the worker right after `ingest_events` for the device (where perimeter alerts are raised),
  wrapped in try/except so it never blocks the cycle.

- [ ] **Step 4: Run — confirm pass.** **Step 5:** full relevant suite + `ruff check app/` + commit
  `feat(reliability): alert on new high-severity service events`. Then push + open PR
  `feat(reliability): service-event ingest + classifier + alerts`. PR body notes the classifier rule set
  is RUNTIME-VERIFY (tune against real reboot/crash/disk events on the box).

---

## PR2 — Frontend: device Reliability tab + Overview card (outline)

> Branch off `main` after PR1 merges. READ the device page tab structure, the existing events/log list
> component, the perimeter Overview card, and `settingHooks`/the API client (`npm run gen:api` if the
> OpenAPI changed — it shouldn't, the events API already exists).

- **Task 4 — Reliability tab:** a new tab on the device page rendering a paginated timeline from
  `GET /api/tenants/{t}/events?source=service&device_id={d}` (keyset `after`, from/to range). Columns:
  time · category · name · severity (color-coded) · process/message. Reuse the existing events-list/keyset
  pattern. Test: renders rows from a mocked events API; paginates.
- **Task 5 — Overview card:** a fleet "Service events (24h)" card — counts by category or devices with a
  recent reboot/crash — via `GET /events/top?field=category&source=service&from=…` (or a small aggregate).
  Reuse the perimeter card. Test: renders the card from mocked data.
- **Task 6 — i18n:** add the new strings to `en.ts` and mirror into all 12 locales (parity enforced by
  `tsc -b`). Gate: `npm run build && npm test && npm run lint`. Commit + PR `feat(reliability): device
  timeline tab + Overview card`.

---

## PR3 — Report section (outline)

> READ `app/services/reporting/sections.py` (SECTION_KEYS, BUILTIN_DEFAULTS, resolve_sections), the report
> service/context, and a section builder + PDF renderer precedent (`failed_logins`/`firewall_blocks`).

- **Task 7 — section registration + builder:** add `"reliability"` to `SECTION_KEYS` and `BUILTIN_DEFAULTS`
  (default **True**). A builder that rolls up `source="service"` events for the report period + device set
  (counts by category; notable reboots/crashes/disk warnings). Tests: builder output with events + empty.
- **Task 8 — PDF rendering:** render the section in the per-client PDF following the existing event-section
  layout; honor the toggle precedence (`BUILTIN_DEFAULTS < tenant < per-device/schedule`). Regenerate the
  demo report if the repo tracks one. Gate: backend tests + `ruff`. Commit + PR `feat(reliability): report
  section`.

---

## Self-review notes
- The only novel logic is `parse_service_events` (PR1 Task 1); everything else reuses the events pipeline,
  events API, alert model, and section registry. No schema/migration.
- The classifier rule set is a RUNTIME-VERIFY starter — flagged in the PR1 body for operator tuning against
  real events. Unit tests pin the classification contract with synthetic lines.
- `event_key`/`_rows`/`parse_ts` signatures must be confirmed in `parsers.py` and matched exactly.
