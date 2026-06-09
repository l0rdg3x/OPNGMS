# OPNGMS — Phase 4: Configuration Management — Design Spec

- **Date:** 2026-06-09
- **Status:** Approved (design); the user delegated decisions and authorized proceeding
- **Phase:** 4 of 5 of the OPNGMS roadmap
- **Depends on:** Phases 1–3 in `main` (devices/secrets/connector/RLS, worker/ARQ, crypto)

---

## 1. Context

**Phase 4** gives OPNGMS **configuration management** for the OPNsense fleet. It is the first phase
that will eventually **mutate** firewall configuration (everything so far — polling, ingest — is
read-only), so it is built **read-side first**: capture and understand configuration before
changing it.

The long-term Phase 4 vision (per the user) is a **firewall-aware configuration UI** that lets an
operator reconfigure **any** piece of a device's config, with the UI **reflecting that specific
firewall** — its NICs/interfaces, its OPNsense version, and its installed plugins (which vary
device to device). That is large and decomposes into milestones; **this spec scopes 4A** (backup +
drift, read-only), the foundation the later milestones build on.

## 2. Hard constraint: version / plugin / NIC tolerance

Different OPNsense devices have **different config shapes**: different NICs/interfaces, different
sections depending on the OPNsense version and installed plugins. OPNGMS must therefore treat the
config as **schema-agnostic**: never assume a fixed `config.xml` structure. The 4A modeling (canonical
hashing, structural diff) walks whatever XML tree is present and handles unknown sections gracefully.
Each snapshot is tagged with the device's **OPNsense version** (already polled as `firmware_version`)
so later milestones can reason version-by-version.

## 3. Phase 4 milestone breakdown

| Milestone | Scope | Mutates FW? |
|-----------|-------|:-----------:|
| **4A** *(this spec)* | Versioned config backup + drift detection + per-path structural diff + query API. Captures config + version + per-firewall structure | ❌ read-only |
| **4B** | Config model + **per-device capability discovery**: parse config into a navigable structure; inventory interfaces/sections/plugins per firewall; begin a per-version feature catalog | ❌ read-only |
| **4C** | **Firewall-aware config UI (read)**: render the config tree reflecting the device's NICs/version/plugins | ❌ read-only |
| **4D** | **Editing + push**: edit any config piece with diff/preview/apply; push to the specific firewall, version/feature-aware. Push runs in **immediate** mode or **scheduled** mode (operator picks a date/time) | ✅ mutates |

The "UI allows everything, version-by-version" goal lives in 4C/4D. The per-version feature catalog
(knowing what each OPNsense version supports) is the heaviest research/data piece — opened in 4B.

**Scheduled push (4D)** is feasible with the existing stack: ARQ supports deferred jobs
(`enqueue_job(..., _defer_until=<datetime>)`), so a scheduled push is a deferred job persisted in
Redis.

> **Deferred-push staleness guard (4D — mandatory).** A push scheduled for a future time is computed
> against the config state *at scheduling time*. If the device's config changes in between (someone
> edits it directly, or another push runs), firing the deferred push blindly would **clobber** those
> intervening changes — unacceptable. **Optimistic concurrency control is required:** when a push is
> scheduled, persist the **baseline `canonical_hash`** (from 4A) of the config it was built against.
> When the deferred job fires, **re-fetch the current config and re-compute its hash**; only apply if
> it still equals the baseline. If it differs, **do NOT apply** — mark the scheduled push as
> `conflict / needs review`, surface it to the operator (with a fresh diff), and require
> re-confirmation. Also serialize pushes per device (no two concurrent pushes to the same firewall).
> 4A's `canonical_hash` and snapshot history are exactly what this guard reuses.

## 4. Security: the config contains secrets

`config.xml` embeds **secrets**: user password hashes, certificate private keys, VPN PSKs, RADIUS
secrets, API keys. Storing and exposing it is sensitive. Therefore:
- Snapshot content is **encrypted at rest** with Fernet (`MASTER_KEY`), like device secrets.
- The default API exposes **metadata + a per-path structural diff that omits values** (it says
  *which* element path changed — added/removed/modified — never the secret value). This is both
  **schema-agnostic** and **secret-safe**.
- **Raw config download** (which would expose secrets) is a **gated + audited** action, deferred
  (later milestone / elevated role) — **not** in 4A.

## 5. Architecture (4A)

```
        ┌──────────────┐  cron (daily)  ┌──────────────┐
        │ ARQ scheduler ├───────────────►│ Redis        │
        └──────────────┘ enqueue          └──────┬───────┘
              backup_device_config(id)            │ consume
                                          ┌────────▼────────┐ OpnsenseClient  ┌──────────┐
                                          │  ARQ worker(s)   ├─────HTTPS──────►│ OPNsense │
                                          └────────┬────────┘ (SSRF-guarded)   │ /backup  │
                                                   │ canonicalize → dedup → encrypt → store
   FastAPI ──RLS──► opngms_app            ┌────────▼─────────────────────────┐
   GET .../config/snapshots, .../diff     │ Postgres: config_snapshots        │
                                          └───────────────────────────────────┘
```

The backup job is trusted backend infrastructure (owner connection, bypasses RLS) like the poller
and ingest. The API reads as `opngms_app` under tenant-context → RLS filters per customer.

## 6. Data model — `config_snapshots` (relational, tenant-scoped, RLS)

```
config_snapshots(
  id              UUID PK,
  tenant_id       UUID NOT NULL,                 -- RLS
  device_id       UUID NOT NULL FK devices ON DELETE CASCADE,
  taken_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  canonical_hash  TEXT NOT NULL,                 -- sha256 of canonical (volatile-stripped) XML
  content_enc     BYTEA NOT NULL,                -- 🔒 Fernet(gzip(raw config.xml))
  opnsense_version TEXT NOT NULL DEFAULT '',     -- device firmware at capture time
  size_bytes      INTEGER NOT NULL DEFAULT 0     -- raw XML size
)
```
- **Dedup-on-change**: a new row is inserted only when `canonical_hash` differs from the device's
  latest snapshot → each row is a **distinct config version**; the version history *is* the drift
  history. (A separate lightweight "last checked" timestamp can live on the device or a small state
  table — decided in the plan; MVP may derive "last checked" from the job run.)
- RLS keyed on `tenant_id` (added to `TENANT_TABLES`; worker owner bypasses, API filters), like
  `alerts`/`events`.
- Index on `(tenant_id, device_id, taken_at DESC)`.

## 7. Connector extension

- `OpnsenseClient.get_config_backup() -> str` → downloads the raw `config.xml` **as text** via
  `/api/core/backup/download/this`. The existing `_get` parses JSON; 4A adds a guarded **raw-text**
  fetch that goes through the same SSRF validation + IP pinning, returning the response body as text.
  Mockable with respx.

⚠️ **Exact OPNsense endpoint TO VERIFY** against a real device (`/api/core/backup/download/this` is
the standard backup endpoint; the response may be raw XML or wrapped). The abstraction + tests do not
change; the mapping is confirmed against a real device.

## 8. Drift detection & structural diff (schema-agnostic + secret-safe)

- **Change detection**: parse the XML, **strip known-volatile nodes** (chiefly the OPNsense
  `<revision>` metadata block — `<time>`/`<description>` change on every save), **canonicalize**
  (recursively sort elements/attributes, normalize whitespace), then `sha256` → `canonical_hash`.
  This avoids false drift from re-saves and works on any version/plugin layout.
- **Per-path structural diff** (`config_diff` service): walk two canonical trees into
  `{element_path: presence}` maps and emit a list of changes — `added` / `removed` / `modified`
  **paths** (e.g. `opnsense/system/user[3]/password`), **without values**. Schema-agnostic (any tree)
  and secret-safe (no leaked values). This is the diff the API returns.
- Pure functions over XML strings → unit-testable without a DB or network.

## 9. Worker

- **Cron `enqueue_config_backups`** (every `CONFIG_BACKUP_INTERVAL`, default daily): lists all devices
  (owner), enqueues `backup_device_config(device_id)` for each.
- **`backup_device_config(device_id)`**: loads device, decrypts secrets, builds `OpnsenseClient`,
  fetches the config, canonicalizes, compares `canonical_hash` to the latest snapshot; on change,
  encrypts (Fernet(gzip(xml))) and inserts a new `config_snapshots` row tagged with the device's
  `firmware_version`. Resilient: a connector error (`OpnsenseError`) is logged and skips the device,
  never failing the job. Idempotent (dedup-on-change).

## 10. Query API (FastAPI, tenant-scoped + RLS)

Under `/api/tenants/{tenant_id}/...`, gated by `require_tenant(DEVICE_VIEW)` + tenant-context (RLS):
- `GET .../devices/{device_id}/config/snapshots` → version history (metadata only: id, taken_at,
  canonical_hash, opnsense_version, size).
- `GET .../devices/{device_id}/config/diff?from=<id>&to=<id>` → per-path structural diff (no values)
  between two snapshots (default `to`=latest, `from`=previous).
- `GET .../devices/{device_id}/config/drift` → summary: latest version time, number of versions,
  whether it changed since the prior snapshot.

(Raw content download is intentionally **not** exposed in 4A — see §4.)

## 11. Foundation for the firewall-aware UI (later milestones)

4A deliberately captures the raw material 4B–4D need so the future UI can "reflect that firewall":
- the **full config** (so every piece is available to model/edit later),
- the **OPNsense version** per snapshot (version-by-version awareness),
- a **schema-agnostic structural tree** (the per-path model already reflects that device's
  interfaces/NICs, present sections, enabled plugins — without assuming a fixed schema).
4B turns this into an explicit navigable model + per-device capability/plugin inventory; 4C/4D render
and edit it.

## 12. Testing
- **Connector**: respx mock of the backup endpoint → `get_config_backup` returns the raw XML text.
- **Canonicalization/diff**: pure-function unit tests — re-save with only `<revision>` changed →
  same `canonical_hash` (no false drift); a real element change → different hash + the correct
  per-path diff; unknown plugin sections handled; **no secret values** appear in the diff output.
- **Backup service**: with a fake client, first run inserts a snapshot; identical config → no new
  row (dedup); changed config → new row; connector error → device skipped, job survives.
- **Storage/crypto**: content stored encrypted (round-trips via Fernet); RLS isolation cross-tenant
  (real `opngms_app`, like events/alerts).
- **API**: tenant-scoped snapshots/diff/drift + cross-tenant isolation test.

## 13. Milestone 4A breakdown (for the plan)
1. **Storage + RLS**: `config_snapshots` model + migration + RLS (`TENANT_TABLES`).
2. **Connector**: `get_config_backup` (raw-text SSRF-guarded fetch) + respx test.
3. **Canonicalize + structural diff** service (pure functions) + tests.
4. **Backup service + worker wiring**: `backup_device_config` (dedup-on-change, encrypt) + cron + job.
5. **Query API**: snapshots / diff / drift endpoints + RLS isolation tests.

Each milestone task = spec→plan→subagent-driven execution.

## 14. Definition of "Done" (4A)
- The worker captures versioned, encrypted config snapshots per device on cadence, deduped on change.
- Drift is detected (canonical hash) tolerant of re-save noise and of version/plugin differences.
- The API exposes version history, a secret-safe per-path structural diff, and a drift summary,
  isolated per customer by RLS (proven by tests).

## 15. Non-goals (4A) / deferred
- **Restore / push** (4B+ / 4D) and **granular alias/rule editing** (4D).
- **Raw config download** (exposes secrets) — gated/audited, later.
- **Firewall-aware editing UI** (4C/4D) and the **per-version feature catalog** (4B).
- **Scheduled push** (immediate/deferred date-time) — a **4D** capability; noted here, built on ARQ
  deferred jobs.
- **Drift → alert** coupling (chose API exposure only).

## 16. Open questions (non-blocking)
- **Exact OPNsense backup endpoint** and response format — verify against a real device; mocked until
  then. Likewise for the 4D push endpoints (firewall alias/filter APIs).
- **Volatile-node allowlist** for canonicalization — `<revision>` is the known one; refine against
  real configs (statistics/cache nodes, if any).
- **Per-version feature catalog** source (4B): how to know what each OPNsense version/plugin set
  supports — scrape docs, derive from configs, or maintain a curated catalog.
- **Snapshot retention**: keep all versions vs cap/prune old ones — decided later (config changes are
  low-volume; full history is cheap).
