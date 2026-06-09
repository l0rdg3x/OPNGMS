# OPNGMS — Phase 1: Foundation & Inventory — Design Spec

- **Date:** 2026-06-08
- **Status:** Approved (design), pending final spec review
- **Author:** l0rdg3x (brainstorming with Claude)
- **Phase:** 1 of 5 of the OPNGMS roadmap

---

## 1. Context

**OPNGMS** (OPNsense Global Management System) is a centralized console to manage and
monitor a fleet of **OPNsense** firewalls from a single pane of glass.

**Audience:** MSPs (Managed Service Providers) managing the OPNsense firewalls of **multiple
clients**, whose data must remain **isolated** from each other. Multi-tenancy, RBAC, and audit
log are therefore part of the core, not optional.

This document specifies **only Phase 1 (Foundation & Inventory)**: the backbone
on which all subsequent phases rely.

## 2. Platform Decisions (valid for all of OPNGMS)

Decided during brainstorming and binding for subsequent phases:

| Topic | Decision |
|-------|----------|
| Audience | Multi-client MSP, mandatory isolation between tenants |
| Device connectivity | **Direct API (pull)**: OPNGMS calls the OPNsense REST API. Firewall reachability is a **deployment precondition** (public/mgmt IP, port-forward, or MSP-managed VPN), not a problem solved by OPNGMS |
| Target scale | Medium: ~dozens of clients, **~100-300 devices** total |
| Stack | **Python/FastAPI backend** (async) + **React/TypeScript frontend**; Postgres as DB |
| Monitoring scope | Essential health/status **+ log/event ingest** aimed at future reports |
| Config scope | Backup/drift + **per-device push** (aliases + firewall rules) |
| Reporting goal | Weekly/monthly PDF reports per client (attacks, visited sites, bandwidth) — **next phase**, but event storage must be modeled from the start to make it possible |

## 3. Roadmap (the 5 phases)

OPNGMS is a multi-subsystem platform: built in **phases**, each with its
own spec → plan → implementation cycle.

1. **Foundation & Inventory** *(this spec)* — multi-tenant data model, auth + RBAC +
   audit, device onboarding, OPNsense connector, FastAPI skeleton + React shell.
2. **Monitoring & Health** — concurrent polling, metrics (up/down, CPU/mem/disk,
   firmware/update, interfaces+traffic, gateways, VPN), time-series storage, dashboard,
   basic alerting.
3. **Log/Event Ingest** — syslog receiver, parsers (firewall, Suricata, DNS/proxy),
   report-ready event storage, basic log search, "visited sites" source selection.
4. **Config Management** — versioned `config.xml` backup + drift detection + restore;
   editing+push of device-by-device aliases and firewall rules with diff/preview/apply.
5. **Reporting** — aggregations on metrics+events, report templates, PDF generation,
   weekly/monthly scheduling + email delivery per client.

## 4. Phase 1 Scope

### In scope
- Multi-tenant data model with double-layer isolation (application + Postgres RLS).
- Session-based AuthN, 4-role RBAC, append-only audit log.
- OPNsense device onboarding with reachability/credentials test and write-only encrypted secrets.
- `OpnsenseClient`: the single abstraction that speaks HTTP with the firewalls.
- Backend skeleton (layered FastAPI) + frontend (React shell with tenant switcher and inventory CRUD).
- Test suite focused on critical invariants (isolation, RBAC, secrets, connector).

### Out of scope (subsequent phases or explicitly deferred)
- Any metrics polling or monitoring dashboard (Phase 2).
- Log/event ingest and reporting (Phases 3 and 5).
- Configuration push or backup (Phase 4).
- SSO/OIDC, 2FA TOTP, notifications.
- Dedicated "Site/Location" entity (for now `site` label + `tags` on the device are enough).

## 5. Architecture (overview)

```
React SPA  ──HTTPS──>  FastAPI (async)
                         ├─ api/        # router per resource (auth, tenants, users, devices, audit)
                         ├─ services/   business logic + tenant scoping
                         ├─ repositories/  DB access (WHERE tenant_id)
                         ├─ connectors/opnsense/  OpnsenseClient ──HTTPS basic-auth──> OPNsense REST API
                         └─ core/       config, crypto, auth, audit, RLS
                         │
                       Postgres (shared schema, tenant_id + RLS)
```

Guiding principle: every unit has **one clear responsibility** and communicates through
well-defined interfaces. `connectors/opnsense` isolates the external boundary; `repositories`
isolate DB access and tenant enforcement.

## 6. Data Model

All tenant-scoped entities carry `tenant_id`. Fields marked `🔒` are encrypted at rest.

- **Tenant** — `id, name, slug, status, note, created_at`. Isolation boundary.
- **User** (MSP staff) — `id, email, name, password_hash, is_superadmin, status, last_login, created_at`.
  Users belong to the MSP organization; access to clients goes through memberships.
- **Membership** (User ↔ Tenant + role) — `id, user_id, tenant_id, role`. Assigns a role
  to a user *within* a client. `SuperAdmin` bypasses memberships.
- **Device** (OPNsense firewall) — `id, tenant_id, name, base_url, api_key🔒, api_secret🔒,
  verify_tls, tls_fingerprint, site, tags, status, last_seen, firmware_version, created_at`.
  Belongs to **exactly one** tenant.
- **AuditLog** — `id, ts, actor_user_id, tenant_id(nullable), action, target_type, target_id,
  ip, details(json)`. Append-only.

Device `status` ∈ `{reachable, unverified, unreachable}`.

## 7. Multi-tenancy & Isolation

Chosen model: **shared schema + `tenant_id`**, with **double-layer** isolation
(defense in depth):

1. **Application layer (mandatory).** A *request context* (middleware) resolves
   the authenticated user and the **active tenant** (from the path, e.g.
   `/api/tenants/{tenant_id}/devices`) and **authorizes access before every handler**
   (valid membership or `is_superadmin`). All tenant-scoped queries go through a
   repository that **always injects** `WHERE tenant_id = :ctx`. Ad-hoc queries that
   could "forget" the filter are not allowed.

2. **Postgres Row-Level Security (RLS).** DB-level policies based on a session variable
   `app.current_tenant`, set on every request. Even if an application bug forgot the
   filter, the DB still blocks the cross-tenant leak. RLS policies are created by
   versioned Alembic migrations.

`SuperAdmin` users operate on all tenants; the context sets `app.current_tenant` to the
active selected tenant even for them (preventing accidental global queries).

## 8. Authentication & Sessions

- **Server-side** sessions with `httpOnly` + `secure` + `SameSite` cookie.
- Email + password login; **argon2** hashing.
- Endpoints: `login`, `logout`, `me`.
- Local accounts only in MVP. SSO/OIDC and 2FA TOTP are deferred (noted as extensions).

## 9. RBAC — Roles and Permission Matrix

Four roles. `SuperAdmin` is a **user-level flag** (MSP staff); the other three are
assigned **per-tenant** via Membership.

- **SuperAdmin** — access to all clients + org administration (CRUD tenants, CRUD users).
- **TenantAdmin** — manages everything inside a client, including that client's memberships.
- **Operator** — operational actions on devices within a client; no user/membership management.
- **ReadOnly** — read-only within a client.

| Action | SuperAdmin | TenantAdmin | Operator | ReadOnly |
|--------|:---:|:---:|:---:|:---:|
| CRUD tenants (org) | ✅ | ❌ | ❌ | ❌ |
| CRUD users (org, global) | ✅ | ❌ | ❌ | ❌ |
| Membership management (within tenant) | ✅ | ✅ | ❌ | ❌ |
| View devices | ✅ | ✅ | ✅ | ✅ |
| Create/edit/delete devices | ✅ | ✅ | ✅ | ❌ |
| Test device connection | ✅ | ✅ | ✅ | ❌ |
| Rotate device secret | ✅ | ✅ | ✅ | ❌ |
| View audit log (scoped to tenant) | ✅ | ✅ | ✅ | ✅ |

Permissions are enforced by a *policy layer* (FastAPI dependency) that evaluates
`(role, action)` against this explicit matrix.

> **Note:** user creation is reserved for `SuperAdmin`. `TenantAdmin`
> "manages memberships" in the sense that they **assign roles to already-existing users** (and
> remove memberships) within their client, but do not create new accounts.

## 10. Audit Log

Every state-changing action writes an **append-only** row: login/logout, device CRUD,
`test`/`reveal`/`rotate` secrets, user/tenant/membership CRUD. Each row records the actor,
tenant, target type+id, IP, and a summary of the changes. The UI will expose it in a
subsequent phase; in Phase 1 it is written and covered by tests.

## 11. Device Onboarding

1. Within a client, the user creates a device: `base_url`, **API key**, **API secret**,
   TLS options (CA verification / fingerprint pinning).
2. The backend runs a **reachability + credentials test**: a lightweight authenticated GET
   to the OPNsense API (firmware/system status).
3. **Success** → save, cache `firmware_version`, mark `status = reachable`.
   **Failure** → save anyway with `status = unverified` and show the **precise** error
   (unresolved DNS / invalid TLS / 401 credentials / timeout), so the user can correct
   without recreating the device.

Saving even unverified devices is a deliberate choice: it allows fixing
credentials/network later without losing the entry.

## 12. Secret Management

- `api_key` / `api_secret` encrypted at rest with **authenticated encryption** (libsodium
  secretbox / Fernet), **master key from environment variable** (KMS/Vault in the future).
- Secrets are **write-only toward the frontend**: after creation they never return to
  the client; the UI shows masked values + a **"rotate"** action.
- Decryption happens **only server-side**, at the moment of the device call.
- Every `reveal`/`rotate`/`test` is written to the audit log.

## 13. OPNsense Connector

A single **`OpnsenseClient`** abstraction encapsulates:
- `base_url`, **HTTP Basic** auth (`api_key` as username, `api_secret` as password);
- TLS verification (with optional fingerprint pinning), timeout, retry/backoff;
- **error normalization**: `AuthError` (401) / `ReachabilityError` (DNS/TLS/timeout) /
  `ApiError(status)` (4xx/5xx) / `ParseError`.

**This is the only point** that speaks HTTP with OPNsense: every other module passes through here. This
isolates the external boundary (easy to mock in tests) and makes it extensible in phases 2-4
without touching consumers.

Phase 1 uses only `test_connection()`, `get_system_info()`, `get_firmware_status()`. The client
is pre-wired to receive a **shared HTTP session** and **per-device concurrency limits**
that will be needed for polling (Phase 2).

> **To verify in implementation:** the exact endpoints for reachability/version
> (presumably `core/firmware/status` and `core/system/...`), confirmed against a
> real OPNsense device or the API docs. The abstraction does not change.

## 14. Backend Structure

```
backend/
  app/
    api/            # router per resource (auth, tenants, users, devices, audit)
    services/       # business logic + tenant scoping
    repositories/   # DB access, enforcement WHERE tenant_id
    models/         # SQLAlchemy (async) + schema
    connectors/
      opnsense/     # OpnsenseClient (single external HTTP boundary)
    core/           # config, crypto/secrets, auth/sessions, audit, RLS deps
    main.py
  migrations/       # Alembic (incl. RLS policies)
  tests/
```

Runtime: SQLAlchemy **async** + Postgres, **Alembic** for migrations (including
RLS policies), **pydantic-settings** for config, **argon2** for passwords.

## 15. Frontend Skeleton

- React + TypeScript with **Vite**.
- **Typed API client, generated from OpenAPI** (no hand-written types that can go out of sync).
- Auth context + **app shell**:
  - **login** page;
  - top bar with **tenant switcher** (for multi-client users / SuperAdmin);
  - side nav;
  - **Device list** for active client (add / edit / test-connection / rotate);
  - device detail stub; admin section (tenants + users) stub.
- Phase 1 is **skeleton + inventory CRUD**, not a dashboard.
- The component library (Mantine vs shadcn/ui) is chosen during the **plan** phase: it is not
  an architectural decision and does not block the design.

## 16. Deployment & Configuration

- `docker-compose` for local development: `api` + Postgres + frontend.
- Deployment target: **single-instance** (adequate for medium scale).
- `.env` for: encryption master key, DB URL, session secret.

## 17. Testing Strategy

TDD on critical invariants:

- **Tenant isolation (top priority):** a user/request in the context of client A
  **cannot** read/write data of client B — tested at service level and via API
  integration. Specific test that **RLS blocks the leak** even if the application filter
  is bypassed.
- **RBAC:** the `(role × action)` matrix becomes a table of test cases.
- **Secret management:** secrets encrypted at rest, **never serialized** in API responses,
  `rotate`/`reveal` recorded in audit.
- **Connector:** mock the HTTP boundary (e.g. `respx`) → verify auth headers, TLS option,
  error normalization (401 → `AuthError`, timeout → `ReachabilityError`, …). No real device
  required.
- **Onboarding:** integration test of the create-device flow with `test_connection` mocked on
  success/failure branches.
- Tooling: **pytest** + httpx test client, factory fixtures, fake OPNsense server.

## 18. Definition of "Done" (Phase 1)

- A SuperAdmin can create tenants and users, and assign memberships with a role.
- A user can authenticate, select a tenant they have access to, and do CRUD of the devices
  of that tenant from the UI.
- Device onboarding runs the connection test and reports the precise result/error.
- Device secrets are encrypted at rest and never leave toward the frontend.
- Cross-tenant isolation is guaranteed at the application level **and** by RLS, with tests that
  prove it.
- The RBAC matrix is enforced and covered by tests.
- State-changing actions are recorded in the audit log.

## 19. Open Questions (for subsequent phases, non-blocking)

- **"Visited sites" data source** (Phase 3/5): Suricata for attacks is clear; for visited
  sites the options are Unbound DNS logs (domain, lightweight), Squid proxy (full URLs),
  or Zenarmor/Sensei (app/web/user visibility, most complete but heavy plugin).
  To be decided in the ingest/reporting phase.
- **UI library** (Mantine vs shadcn/ui): to be decided during the plan phase.
