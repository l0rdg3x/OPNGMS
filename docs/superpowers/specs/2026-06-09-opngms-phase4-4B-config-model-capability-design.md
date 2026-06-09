# OPNGMS — Phase 4 / Milestone 4B: Config Model + Capability Discovery — Design Spec

- **Date:** 2026-06-09
- **Status:** Approved (design); the user delegated the nuanced decisions and authorized proceeding
- **Phase:** 4 of 5 — Milestone 4B (after 4A backup/drift)
- **Depends on:** 4A (`config_snapshots`, encrypted; connector; `config_diff`/defusedxml) in `main`
- **Enables:** 4C (firewall-aware config UI) and 4D (edit + push)

---

## 1. Context

**4B** turns the raw config snapshots captured in 4A into two read-only, per-device artifacts that the
firewall-aware UI (4C) and editing (4D) build on:
1. a **navigable config model** (a schema-agnostic JSON tree of the device's config), and
2. a **capability inventory** (what that firewall *has* and *can have*: interfaces/NICs, configured
   sections, available plugins/modules, OPNsense version).

Everything is computed **on-demand** from the latest 4A snapshot (decrypted + parsed server-side) plus
a live capability probe of the device. No new storage. Read-only — no mutation (that is 4D).

## 2. Two design constraints from the user (resolved)

**(a) "Edit everything — including secrets — without leaking them."** Resolved with the
**write-only / leave-blank-to-keep** pattern (the same OPNGMS already uses for device secrets):
- The model **shows non-sensitive values** and **redacts sensitive ones** (passwords, API keys,
  private keys, PSKs…) — the secret value **never leaves the server**; the node carries only a
  `sensitive: true` flag and `value: null`.
- To **change** a sensitive field, the operator types a **new** value (pushed in 4D). If left
  untouched/blank, the existing secret is **preserved** on push. So the full config — secrets
  included — is editable without the API ever exposing an existing secret. 4B's job is to **flag**
  sensitive nodes so 4C/4D can apply this pattern.

**(b) "Configure what the version supports but isn't in the config yet."** Empirical-from-config only
reveals what is *already* configured. So 4B also discovers what the device *can* do by **probing the
device itself** (installed plugins/modules + version via its API) rather than maintaining a huge
hand-curated per-version catalog. A small, extensible **capability registry** maps plugin/module ids
to capability descriptors. The exhaustive field-level per-version schema (for rich edit forms) is
deferred to 4D, where it is best sourced from the device.

## 3. Architecture

On-demand, read-only, tenant-scoped + RLS (reuses `config_snapshots` from 4A — already RLS-isolated):

```
   FastAPI ──RLS──► opngms_app                          ┌── latest config_snapshots row (encrypted)
   GET .../config/model                                 │   → decrypt+gunzip (server-side)
   GET .../config/capabilities ──► services ────────────┤   → defusedxml parse → build_tree / inventory
                                       │                 │
                                       └── OpnsenseClient ─► OPNsense  (live plugin/version probe)
```

## 4. Config model service (`app/services/config_model.py`, pure functions)

- `build_tree(xml: str) -> dict`: schema-agnostic JSON tree. Each node:
  `{ tag, path, attributes: {...}, children: [...], value: str|None, sensitive: bool }`.
  - Leaf nodes carry `value`; container nodes carry `children`.
  - Reuses 4A's parsing: **defusedxml** (XXE/billion-laughs safe), strips the volatile `<revision>`,
    preserves element order, indexes repeated siblings by position (`tag[n]`) — same path scheme as
    the 4A diff (so model paths and diff paths align).
- **Sensitive redaction** (`is_sensitive(tag)`): a **conservative** tag denylist (case-insensitive
  substrings such as `password`, `passwd`, `secret`, `psk`, `pre-shared-key`, `preshared`,
  `passphrase`, `privatekey`, `private_key`, `apikey`, `api_key`, `sharedkey`, `token`, `prv`). When a
  leaf tag matches, the node is emitted with `sensitive: true` and `value: null` (redacted). **Prefer
  over-redaction**: when in doubt, redact. This denylist is a maintained security control.
- Pure functions over the XML string → unit-testable without DB/network. **No secret value is ever
  placed into a sensitive node's output.**

## 5. Capability discovery (`app/services/capability.py` + connector)

A per-device inventory merging three sources:
- **Empirical (from the config snapshot)**:
  - **Interfaces/NICs**: parsed from `<interfaces>` (logical name → assigned NIC, description).
  - **Configured sections**: the set of top-level config sections present.
  - **OPNsense version**: from the snapshot's `opnsense_version`.
- **Live device probe** (new connector method `get_plugin_info()` → installed plugins + product
  version; ⚠️ exact endpoint TO VERIFY, presumably `core/firmware/info`): the set of installed
  plugins/modules. Resilient — a probe failure degrades to empirical-only (still useful).
- **Capability registry** (`app/services/capability_registry.py`, small + extensible): maps known
  plugin/module ids → a human-friendly capability descriptor (id, label, config area, e.g.
  `os-wireguard → {label: "WireGuard VPN", area: "vpn/wireguard"}`). Seeded with common
  core/plugins; unknown ids pass through with a generic descriptor.
- Output `CapabilityInventory`: `{ opnsense_version, interfaces: [...], configured_sections: [...],
  available_capabilities: [...] }` — so the UI can show both *what is configured* and *what could be*.

## 6. API (FastAPI, tenant-scoped + RLS)

Under `/api/tenants/{tenant_id}/...`, gated by `require_tenant(DEVICE_VIEW)` + tenant-context (RLS):
- `GET .../devices/{device_id}/config/model` → the navigable tree (values + redacted sensitive). 404
  if the device has no snapshot yet.
- `GET .../devices/{device_id}/config/capabilities` → the capability inventory.

Both read the **latest** `config_snapshots` row for the device (tenant-scoped via the existing
repository + RLS), decrypt it server-side, and derive their output. No raw config or secret value is
ever returned.

## 7. Security

- **Conservative redaction**: the sensitive denylist errs toward over-redaction; completeness is a
  maintained, security-sensitive control (tracked as debt — a missed secret tag would be a leak).
- **Secrets never leave the server**: the model carries only the `sensitive` flag; the write-only
  editing pattern (4D) closes the loop so editing never needs the existing secret.
- **Hostile XML**: parsing reuses 4A's defusedxml path (XXE/billion-laughs refused; malformed config →
  graceful error, not a crash).
- **Tenant isolation**: same RLS + tenant-filter as 4A; an isolation test proves cross-tenant
  separation via the real `opngms_app` role.

## 8. Milestone 4B breakdown (for the plan)
1. **Config model service**: `build_tree` + `is_sensitive` redaction (pure functions) + tests
   (structure, redaction of sensitive leaves, no secret value in output, order/paths align with 4A).
2. **Connector `get_plugin_info`**: live device probe (reuses the SSRF-guarded `_get`) + respx test.
3. **Capability service + registry**: empirical (interfaces/sections/version) + probe merge +
   registry mapping; resilient to probe failure + tests.
4. **API**: `/config/model` + `/config/capabilities` endpoints (latest snapshot, decrypt server-side)
   + RLS isolation tests + the "no secret value leaks" assertions.

Each task = subagent-driven (implementer + spec/quality review).

## 9. Definition of "Done" (4B)
- `GET /config/model` returns a schema-agnostic navigable tree of the device's latest config, with
  **sensitive values redacted** (flagged, never emitted), order-preserving, defusedxml-safe.
- `GET /config/capabilities` returns the device's interfaces, configured sections, OPNsense version,
  and available plugins/modules (empirical + live probe), resilient to probe failure.
- Both are tenant-scoped and RLS-isolated (proven by a real-`opngms_app` test); no secret value
  appears in any response.

## 10. Non-goals (4B) / deferred
- **Editing / push** (4D) — 4B only flags `sensitive` to enable the write-only pattern later.
- **Exhaustive field-level per-version schema catalog** (4D, device-sourced) — 4B uses a small
  plugin/module registry, not a full field schema.
- **The config UI** (4C).
- **Precomputed/stored model** — chose on-demand from the latest snapshot.
- **Raw config / secret value exposure** — never.

## 11. Open questions (non-blocking)
- **Plugin/version endpoint** for the live probe (`core/firmware/info`?) — verify against a real
  device; mocked with respx until then. Probe failure degrades gracefully to empirical-only.
- **Sensitive denylist completeness** — the security-critical list; refine against real configs,
  erring toward over-redaction. Consider a value-shape heuristic as a secondary guard later.
- **Interfaces parsing** — the exact `<interfaces>` shape/fields to verify against a real config.
- **Capability registry seed** — which plugin ids to seed; unknown ids pass through generically.
