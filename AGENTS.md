# AGENTS.md — guide for LLM/agent contributors

This file gives an AI coding assistant (Claude Code, Cursor, Copilot, Aider, etc.) the project-specific
context it needs to make correct, mergeable changes to **OPNGMS**. Human contributors will find it
useful too. It is intentionally about the *non-obvious* rules — read it before writing code.

> Tooling note: `CLAUDE.md` simply points here, so Claude Code and any AGENTS.md-aware tool share one
> source of truth. Keep this file up to date when conventions change.

---

## What OPNGMS is

A multi-tenant MSP console to manage and monitor a fleet of OPNsense firewalls. Backend: **Python 3.14 /
FastAPI / SQLAlchemy 2.0 async + asyncpg / Alembic / TimescaleDB (PostgreSQL 16 + Row-Level Security) /
ARQ + Redis**. Frontend: **React 19 / TypeScript / Vite / Mantine v9** SPA. Ships as multi-arch GHCR
images run via Docker Compose. Deep docs live in the [Wiki](https://github.com/l0rdg3x/OPNGMS/wiki);
design specs + plans live under `docs/superpowers/`.

## Repository layout

| Path | What lives here |
|------|-----------------|
| `backend/app/` | FastAPI app: `main.py`, routers (`api/`), `models/`, `services/`, `worker.py`, `core/config.py`, RLS setup (`db.py`/`rls.py`/`db_roles.py`) |
| `backend/tools/opnsense_catalog/` | Offline catalog generator for the version-aware config editor |
| `backend/alembic/` | DB migrations (forward-only in practice) |
| `backend/tests/` | pytest suite (needs a live TimescaleDB) |
| `frontend/src/` | React SPA: `pages/`, `components/`, `api/` (typed client), `i18n/` (UI dictionaries), `tenant/`, `auth/` |
| `docker-compose*.yml` | `prod` (core) + `full` (core + log lake) + overlays `logs`/`logs.multinode`/`tls`/`caddy`/`traefik` |
| `.github/workflows/` | CI + security (Trivy, gitleaks, dependency-review, scheduled audit) + publish-images / publish-catalogs |
| `docs/superpowers/` | `specs/` and `plans/`, one per milestone |

## Environment & setup

**Backend** (Python 3.14; venv at `backend/.venv`):
```bash
cd backend && docker compose up -d db redis     # infra only
python -m venv .venv && . .venv/bin/activate
pip install -e .[dev]
export ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms
export DATABASE_URL=postgresql+asyncpg://opngms_app:opngms_app@localhost:5432/opngms
export ALEMBIC_DATABASE_URL="$ADMIN_DATABASE_URL"   # alembic reads this
export SESSION_SECRET="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')"
export MASTER_KEY="$(python -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')"
alembic upgrade head
uvicorn app.main:app --reload          # API on :8000
arq app.worker.WorkerSettings          # worker (separate shell, same env)
```

**Frontend** (Node.js 24+):
```bash
cd frontend && npm ci --legacy-peer-deps   # peer-dep conflict requires the flag
npm run gen:api                            # regenerate the typed API client from the backend OpenAPI
npm run dev                                # Vite on :5173, proxies /api → :8000
```

## Build / test / lint — run these before you open a PR

| | Command | Notes |
|--|---------|-------|
| Backend tests | `cd backend && python -m pytest -q` | Needs a reachable TimescaleDB. Tests use `TEST_DATABASE_URL` / `ADMIN_DATABASE_URL` = `postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test`. `asyncio_mode = auto`. |
| Backend lint | `cd backend && ruff check app/` | `line-length = 100`. |
| Frontend build | `cd frontend && npm run build` | **This is the gate.** `build = tsc -b && vite build`; `tsc -b` type-checks the **tests** too, so `tsc --noEmit` / `vitest` alone is not enough — CI fails if `npm run build` fails. |
| Frontend tests | `cd frontend && npm test` | Vitest (`vitest run`). |
| Frontend lint | `cd frontend && npm run lint` | `eslint .`. |

## Invariants you must NOT break

1. **Tenant isolation via RLS.** Every tenant-scoped table has `tenant_id` + a fail-closed RLS policy.
   The API connects as the non-superuser **`opngms_app`** role (RLS enforced) and sets the per-request
   tenant context; the worker/migrations connect as the owner (RLS-exempt, trusted infra, never
   user-facing). Never run user-facing queries as the owner, never disable/weaken RLS, never trust a
   client-supplied `tenant_id` over the RBAC-verified path.
2. **Secrets at rest.** Device API credentials and the SMTP password are Fernet-encrypted with
   `MASTER_KEY`; they are never returned by the API and never logged. Don't add code that returns or
   logs them. Don't commit real secrets — they come from the environment / files outside the tree.
3. **Fail-closed config.** The API refuses to start if any guarded secret still contains `change-me`.
   Keep that guard intact.
4. **Outbound safety.** All calls to managed boxes go through the SSRF-guarded `OpnsenseClient` (HTTPS
   only, no redirects, blocks loopback/link-local incl. cloud metadata, optional TLS pinning). Don't
   add unguarded outbound HTTP. `verify_tls=False` is only acceptable for explicit ephemeral probes.
5. **Sessions.** Server-side sessions signed with `SESSION_SECRET`; `Secure`/`HttpOnly` cookies → HTTPS
   is mandatory behind a proxy that forwards `X-Forwarded-Proto: https`.

## Conventions

- **English everywhere** in the repo — code, comments, UI strings, commit messages, PR text. (Chat with
  your human in whatever language they prefer, but committed artifacts are English.)
- **Match the surrounding code**: its naming, comment density, and idioms. Prefer small, focused files.
- **Frontend i18n key parity is compiler-enforced.** UI strings live in `frontend/src/i18n/en.ts`. The
  12 sibling locale dictionaries are typed `: Dict` (en's `Dict` is widened to string leaves), so
  **adding/removing/renaming a key in `en.ts` breaks the build until you mirror it in every locale**
  (`it es fr de pt nl ru ar zh zhTW ja`). Add the English key first, then update all locales. Don't
  hardcode user-facing strings in components — add a key and use `useT()`.
- **Commits**: conventional-style subject (`feat:`, `fix:`, `docs:`, `chore:` …), imperative mood,
  body explaining the *why*.

## Contribution workflow

`main` is **protected** — no direct pushes. Every change is a PR:
1. Branch off `main` (`feat/…`, `fix/…`, `docs/…`, `chore/…`).
2. Make the change; run the full build/test/lint locally (table above).
3. Open a PR. It must be **up to date with `main`** and pass **all required checks**, then **squash-merge**.
4. For non-trivial features, follow the spec → plan flow used across the repo (`docs/superpowers/`):
   brainstorm a short design, write a plan, implement task-by-task with review.

### CI checks that must pass
- **Backend tests** (Python 3.14 + TimescaleDB), **Backend lint** (ruff), **Frontend (test, build,
  lint)** (Node 24), **Dependency audit** (pip-audit + npm audit).
- **Security**: Trivy (image scan, SARIF → code scanning), gitleaks (secret scan), Dependency Review,
  and **CodeQL** — note CodeQL runs via **GitHub "default setup"**, so there is *no* `codeql.yml` in
  `.github/workflows/`; don't assume it's absent just because there's no workflow file.

## Gotchas / FAQ

- **Frontend build gate** — always run `npm run build`, not just `vitest`/`tsc --noEmit`. `tsc -b`
  type-checks test files; CI's "Frontend" job runs the full build.
- **`localStorage` is unavailable in the test runner** — production code wraps storage access in
  try/catch; in tests, stub it (`vi.stubGlobal("localStorage", …)`) rather than relying on a real one.
- **Mantine RTL** — Arabic flips layout via `DirectionProvider` + the `DirectionSync` bridge that sets
  `<html dir>`. New locales go in `frontend/src/i18n/locale.ts` (`SUPPORTED_LOCALES`, `LOCALE_LABELS`,
  and `RTL_LOCALES` if right-to-left) and the `dictionaries` map in `i18n/index.ts`.
- **gitleaks** — the public `frontend/src/i18n/*.ts` string tables are path-allowlisted in
  `.gitleaks.toml` because the entropy heuristic false-positives on translated labels; real secrets are
  still scanned everywhere else (suppression is by match-string, not path, for the rest).
- **`--legacy-peer-deps`** is required for `npm ci`/`install` (a peer-dep range conflict).
- **Alembic** reads `ALEMBIC_DATABASE_URL`. Migrations are forward-only in practice — rollback is
  restore-from-backup, not a down-migration.

## Where to look next

- Operator/contributor manual: the **[Wiki](https://github.com/l0rdg3x/OPNGMS/wiki)** (Installation,
  Configuration, Architecture, Configuration-Editor, Log-Lake, Security, Development, Troubleshooting).
- Per-milestone design rationale: `docs/superpowers/specs/` and `docs/superpowers/plans/`.
- High-level overview + status: `README.md`.
