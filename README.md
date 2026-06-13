# OPNGMS — OPNsense Global Management System

A multi-tenant console for MSPs to **manage and monitor a fleet of [OPNsense](https://opnsense.org/)
firewalls** from a single pane of glass: device inventory, health & network monitoring, alerting,
security/event ingest, per-customer white-label PDF reporting **with scheduled email delivery**,
configuration templates, a **version-aware OPNsense config editor**, and configuration backup/drift.

[![CI](https://github.com/l0rdg3x/OPNGMS/actions/workflows/ci.yml/badge.svg)](https://github.com/l0rdg3x/OPNGMS/actions/workflows/ci.yml)
[![Container Image Scan](https://github.com/l0rdg3x/OPNGMS/actions/workflows/trivy.yml/badge.svg)](https://github.com/l0rdg3x/OPNGMS/actions/workflows/trivy.yml)
[![Secret Scan](https://github.com/l0rdg3x/OPNGMS/actions/workflows/gitleaks.yml/badge.svg)](https://github.com/l0rdg3x/OPNGMS/actions/workflows/gitleaks.yml)

Tenant isolation is **structural**, not advisory: a shared schema with `tenant_id` and Postgres
**Row-Level Security** (`ENABLE` + `FORCE`, fail-closed), with the API running as a non-superuser role.

> **📖 Full documentation lives in the [Wiki](https://github.com/l0rdg3x/OPNGMS/wiki).**
> Detailed guides: [Installation](https://github.com/l0rdg3x/OPNGMS/wiki/Installation) ·
> [Configuration](https://github.com/l0rdg3x/OPNGMS/wiki/Configuration) ·
> [Architecture](https://github.com/l0rdg3x/OPNGMS/wiki/Architecture) ·
> [Configuration Editor](https://github.com/l0rdg3x/OPNGMS/wiki/Configuration-Editor) ·
> [Log Lake](https://github.com/l0rdg3x/OPNGMS/wiki/Log-Lake) ·
> [Reporting](https://github.com/l0rdg3x/OPNGMS/wiki/Reporting) ·
> [Security](https://github.com/l0rdg3x/OPNGMS/wiki/Security) ·
> [Upgrading](https://github.com/l0rdg3x/OPNGMS/wiki/Upgrading) ·
> [Development](https://github.com/l0rdg3x/OPNGMS/wiki/Development) ·
> [Troubleshooting](https://github.com/l0rdg3x/OPNGMS/wiki/Troubleshooting). This README is the overview.

---

## Contents

- [Features](#features)
- [Screenshots](#screenshots)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Repository layout](#repository-layout)
- [Getting started](#getting-started)
- [Project status](#project-status)
- [Changelog](CHANGELOG.md)
- [Tests](#tests)
- [Security & multi-tenancy](#security--multi-tenancy)
- [License](#license)

---

## Features

- **Inventory** — onboard customer firewalls with encrypted API credentials and reachability tests.
- **Monitor** — periodic OPNsense-API polling into TimescaleDB hypertables: health (CPU/mem/disk,
  uptime, firmware), network (interfaces, gateways, VPN), and up/down status.
- **Alerting** — threshold-based alerts evaluated on every poll, with an active/historical view.
- **Event ingest** — incremental, deduplicated pull of Suricata IDS/IPS alerts and DNS queries.
- **Reporting** — per-customer white-label PDF reports with an **executive-summary KPI band** and
  per-device sections (health, alerts & connectivity, firmware & config, attacks, web, data, status),
  each **toggleable per tenant and per device**; localized across **all 12 languages** (incl. RTL Arabic),
  with **scheduled email delivery** (per-tenant **and** per-device, weekly/monthly/on-demand) via a
  superadmin SMTP relay, white-label sender, "send now", and send-retry.
  **[See sample reports →](docs/demo-reports/)**
- **Config management** — versioned, encrypted backup with drift detection, targeted **revert**, and a
  firewall-aware editing UI; optional **live config push** behind a default-OFF master switch.
- **Device actions** — firmware update / major upgrade and plugin install/remove (now or scheduled), run
  by a reboot-tolerant worker, plus a WebGUI deep-link.
- **Configuration templates** — reusable, value-controlled templates in a shared MSP library with
  per-customer overrides and a redacted preview. Five kinds (firewall aliases, any introspectable
  setting, Suricata/IDS rulesets, firewall rules, Monit tests) plus **profiles** (ordered bundles).
- **Version-aware config editor** (flagship) — edit **every API-modifiable OPNsense setting** from an
  **OPNsense-like editor** matched to each device's firmware version, driven by a versioned, SHA-256-
  verified **catalog**; live-value forms, scalars + grids, pushed through the safe config pipeline.
- **Two-factor auth** — optional/enforceable **TOTP** login with recovery codes, an enforcement policy,
  and superadmin / break-glass recovery.
- **Log lake** (optional) — managed firewalls ship syslog over **mTLS** into **OpenSearch**;
  enable/rotate/revoke forwarding per device, investigate from a tenant-scoped **Logs** page, and watch
  the estate from a superadmin **Log fleet** dashboard.
- **Localized, multi-tenant UI** — fleet overview + per-device charts; **12-language** SPA with full RTL.

## Screenshots

A dark, instrument-grade "operations console" UI (Mantine v9 + IBM Plex), built for SOC/NOC workflows
and localized into **12 languages** (with full right-to-left support).

| Fleet overview | Version-aware config editor (OPNsense-like) |
|---|---|
| [![Overview](docs/ui/overview.png)](docs/ui/overview.png) | [![Config editor](docs/ui/config-editor.png)](docs/ui/config-editor.png) |

> **📸 Full gallery → [Screenshots wiki page](https://github.com/l0rdg3x/OPNGMS/wiki/Screenshots)** — devices &
> health, the GeoIP attacker-countries view, templates, reporting, MFA, access groups, the log fleet, RTL, and more.
> Sample PDF reports live under [`docs/demo-reports/`](docs/demo-reports/).

| Right-to-left layout (Arabic) | Access groups (group-based RBAC) |
|---|---|
| [![RTL](docs/ui/rtl.png)](docs/ui/rtl.png) | [![Groups](docs/ui/groups.png)](docs/ui/groups.png) |

## Architecture

```
              ┌───────────────┐   cron         ┌───────────────┐
              │ ARQ scheduler │───────────────►│ Redis (broker)│
              └───────────────┘  enqueue jobs   └──────┬────────┘
   poll_device / ingest_device_events / enqueue_due_reports     │
                                              ┌─────────▼────────┐  OpnsenseClient   ┌──────────┐
                                              │   ARQ worker(s)  │──────HTTPS───────►│ OPNsense │
                                              └────┬──────┬──────┘  (SSRF-guarded,   │ sys, IDS │
                                       PDF reports │      │ aiosmtplib  TLS pin)      └──────────┘
                                          ┌────────▼──┐  ┌▼───────────────┐
                                          │ WeasyPrint│  │ SMTP relay     │──► report recipients
                                          └───────────┘  └────────────────┘
                                                        │ metrics / status / alerts / events
  React + Mantine ──HTTP──► FastAPI ──RLS──►  ┌─────────▼─────────────────────────┐  (owner, RLS-exempt)
  (SPA, nginx)              (opngms_app role)  │ TimescaleDB: metrics & events      │
                                               │ (hypertables) + tenants, devices,  │
                                               │ alerts, sessions, reports,         │
                                               │ smtp_settings, report_schedule, …  │
                                               └────────────────────────────────────┘
```

- **API** — async FastAPI. Session auth + per-session CSRF, 4-role RBAC, tenant-scoped endpoints.
  Connects as the non-superuser `opngms_app` role, so RLS filters every read per customer.
- **Worker** — ARQ + Redis. Cron jobs enqueue per-device work and fire due report schedules.
  `OpnsenseClient` is the single outbound HTTP boundary (SSRF guard + optional cert pinning). Runs as
  the DB owner (RLS-exempt: trusted infrastructure, never user-facing).
- **Frontend** — Vite + React 19 + Mantine v9 SPA with a typed API client generated from the backend
  OpenAPI schema, served by nginx which also reverse-proxies `/api` (same origin → no CORS).

Full component diagram, data flows, and the multi-tenancy model:
**[Architecture wiki](https://github.com/l0rdg3x/OPNGMS/wiki/Architecture)**.

## Tech stack

| Area | Technologies |
|------|--------------|
| Backend | Python 3.14, FastAPI, SQLAlchemy 2.0 async + asyncpg, Alembic, Pydantic v2 |
| Storage | TimescaleDB (PostgreSQL 16 + extension), hypertables for metrics & events, Row-Level Security; **OpenSearch** (Apache-2.0) for the optional log lake |
| Worker | ARQ + Redis |
| Email | aiosmtplib (STARTTLS / implicit TLS / plain), Fernet-encrypted SMTP credentials |
| Security | argon2 (passwords), Fernet (device & SMTP secrets), TOTP MFA (pyotp), Postgres RLS, SSRF guard, TLS pinning, defusedxml |
| Reporting | WeasyPrint (HTML/CSS → PDF) + Jinja2 (autoescape) + hand-built SVG charts |
| Frontend | Vite, React 19, TypeScript, Mantine v9, TanStack Query, React Router, openapi-fetch |
| Testing | pytest + pytest-asyncio + respx (backend); Vitest + Testing Library + MSW (frontend) |

## Repository layout

```
backend/             FastAPI API, ARQ worker, OPNsense connector, models, Alembic migrations, tests
backend/tools/       offline OPNsense catalog generator (for the version-aware editor)
frontend/            React/Mantine SPA (shell, pages, typed API client, i18n, tests); nginx/ serving
docs/superpowers/    design specs and implementation plans, one per milestone
docs/ui/             UI screenshots used in this README
deploy/              Caddy/syslog-ng/OpenSearch config for the overlays
docker-compose*.yml  prod (core) + full (core+log lake) + overlays: logs / logs.multinode / tls / caddy / traefik
.env.example         every deployment variable, documented
.github/workflows/   CI (tests, lint, audit) + security (Trivy, gitleaks, dependency-review, scheduled audit) + publish-images (GHCR) + publish-catalogs
AGENTS.md            guide for LLM/agent contributors (CLAUDE.md points to it)
```

## Getting started

**Deploy (production).** OPNGMS ships pre-built multi-arch images from GHCR
(`ghcr.io/l0rdg3x/opngms-{backend,frontend}`, `amd64`+`arm64`) run via Docker Compose:

```bash
cp .env.example .env      # set strong secrets — the API fails closed on `change-me` placeholders
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

A one-shot `migrate` service applies the schema (and DB upgrades) before `api`/`worker` start. **HTTPS
is mandatory in production** — pick a TLS model (behind your proxy / built-in nginx cert / automatic
**Caddy** or **Traefik**), then complete first-run (create the superadmin, configure SMTP, onboard
tenants & devices). The all-in-one `docker-compose.full.yml` also brings up the optional log lake.
Step-by-step (prerequisites, the four TLS models, first run, upgrades, backups, `MASTER_KEY` rotation):
the **[Installation](https://github.com/l0rdg3x/OPNGMS/wiki/Installation)**,
**[Upgrading](https://github.com/l0rdg3x/OPNGMS/wiki/Upgrading)**, and
**[Log Lake](https://github.com/l0rdg3x/OPNGMS/wiki/Log-Lake)** wiki pages.

**Develop.** Requirements: Docker + Compose, Python 3.14, Node.js 24+. Bring up the infra (TimescaleDB +
Redis), run the API (`uvicorn`) + worker (`arq`) + the Vite dev server. Full setup, the build/test/lint
commands, and the contribution flow are in the
**[Development](https://github.com/l0rdg3x/OPNGMS/wiki/Development)** wiki page (and
[`AGENTS.md`](AGENTS.md) for LLM/agent contributors).

## Project status

Per-release notes live in [`CHANGELOG.md`](CHANGELOG.md) and on the
[GitHub Releases page](https://github.com/l0rdg3x/OPNGMS/releases) (published automatically from each
version tag).

| Area | Status |
|------|--------|
| **Foundation & inventory** — auth/RBAC, org admin, device onboarding, encrypted secrets, SPA shell | ✅ Done |
| **Monitoring** — poller, health + network metrics, alerting, dashboard | ✅ Done |
| **Event ingest** — Suricata IDS + DNS into the `events` hypertable, keyset-paginated query API | ✅ Done |
| **PDF reporting** — white-label per-tenant reports, scheduled + on-demand, 7-language localization | ✅ Done |
| **Report email delivery** — per-tenant **and per-device** schedules; one superadmin SMTP relay (test-send); white-label sender; "send now"; hourly cron + send-retry | ✅ Done |
| **Config management** — encrypted backup, drift detection, targeted revert, firewall-aware UI, default-OFF live push | ✅ Done |
| **OPNsense connector** — telemetry verified on real 26.1.9; **(edition, version)-aware** endpoint matrix (Community / Business) | ✅ Done |
| **Device actions** — firmware update / multi-step upgrade + plugin install/remove (now or scheduled), WebGUI deep-link | ✅ Done |
| **Configuration templates** — MSP **library** + per-tenant overrides + typed apply + **profiles**; five kinds (alias, generic setting, IDS rulesets, firewall rules, Monit tests) | ✅ Done |
| **Version-aware config editor** (flagship) — catalog **generator** + **distribution** (6-hourly publish, SHA-256-verified, DB-cached) + generic apply + **OPNsense-like editor** (menu tree + search, live-value forms, scalars + grids), **cross-version diff badges**, and a read-only live **`config.xml` map** cross-referenced to the catalog. 3a/3b/3c merged; remaining: sub-project 4 (Business proprietary deltas) | ✅ Done |
| **Login MFA (TOTP)** — second factor + recovery codes, enforcement policy (off/all/privileged), two-step login, break-glass CLI | ✅ Done |
| **Localization** — **12-language** UI (en/it/es/fr/de/pt/nl/ru/ar/zh/zh-TW/ja) incl. full **RTL** (Arabic) | ✅ Done |
| **Deployment** — multi-arch **GHCR** images (semver-tagged), TLS overlays (proxy / cert / Caddy / Traefik), one-shot auto-migrate, configurable timezone | ✅ Done |
| **Hardening** — web headers, TLS pinning, session lifecycle, `MASTER_KEY` rotation, CI security suite, protected `main` | ✅ Done |
| **Log lake** — opt-in mTLS syslog-ng → OpenSearch; tenant-scoped **Logs** page; per-device cert lifecycle (rotate/revoke); deep paging + multi-node HA; cross-tenant **Log fleet** dashboard | ✅ Done |

Detailed per-milestone design specs + implementation plans live in
[`docs/superpowers/`](docs/superpowers/); feature documentation is in the
[Wiki](https://github.com/l0rdg3x/OPNGMS/wiki).

## Tests

```bash
# Backend (needs a reachable test TimescaleDB)
cd backend
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test \
ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test \
.venv/bin/python -m pytest -q

# Frontend
cd frontend
npm test            # Vitest
npm run build       # tsc typecheck + production build
npm run lint        # ESLint
```

## Security & multi-tenancy

- **Tenant isolation** — every tenant-scoped table carries a `tenant_id` + a fail-closed RLS policy
  (`ENABLE` + `FORCE`); the API runs as the non-superuser `opngms_app` role and sets the tenant context
  per transaction (cross-tenant isolation covered by SQL-level and real-API tests).
- **Secrets at rest** — every secret (device API credentials, config snapshots, MFA TOTP secrets, the SMTP
  password, the syslog CA key) is Fernet-encrypted with `MASTER_KEY`, never returned by any API or logged.
  `MASTER_KEY` rotation is **zero-downtime**: the crypto layer (`MultiFernet`) encrypts with the new primary
  key but decrypts with the new key **or** any retired key in `MASTER_KEY_OLD_KEYS`, so you add the new key,
  run the bundled re-key script (`python -m app.scripts.rekey_secrets`, which re-encrypts every column), and
  only then retire the old key — no gap. Full procedure: [Upgrading → Rotating MASTER_KEY](https://github.com/l0rdg3x/OPNGMS/wiki/Upgrading#rotating-master_key).
- **Auth & sessions** — argon2 passwords; session tokens stored only as a SHA-256 hash; per-session
  CSRF; idle + absolute expiry; optional/enforceable **TOTP MFA** with recovery codes and break-glass.
- **Outbound & transport** — SSRF-guarded connector (HTTPS only, blocks loopback/link-local incl. cloud
  metadata) + optional TLS fingerprint pinning; mandatory HTTPS for the SPA; mTLS for the log lake.
- **Web hardening & CI assurance** — CSP/HSTS/nosniff headers, login rate-limiting, defusedxml; an
  application-security test suite (CSRF/RLS/SSRF/redaction/XXE) + dependency audit run in CI alongside
  **CodeQL** (GitHub default setup), Dependabot + Dependency Review, Trivy, and gitleaks; `main` is
  protected and requires these checks before merge.

Full threat model and operator hardening checklist:
**[Security wiki](https://github.com/l0rdg3x/OPNGMS/wiki/Security)**. Report a vulnerability via
[`SECURITY.md`](SECURITY.md).

## License

See [LICENSE](LICENSE).

## Attribution

IP geolocation (attacker-countries breakdown) uses the **DB-IP Lite** database by
[DB-IP](https://db-ip.com), licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The
database is fetched and refreshed independently of the application (see the `publish-geoip` workflow).

## Trademarks & disclaimer

OPNsense® is a registered trademark of Deciso B.V. All other product names, logos, and brands are the
property of their respective owners. OPNGMS is an **independent**, third-party project and is **not
affiliated with, endorsed by, sponsored by, or supported by** Deciso B.V. or the OPNsense project.
References to "OPNsense" are used solely for identification and to describe interoperability.
