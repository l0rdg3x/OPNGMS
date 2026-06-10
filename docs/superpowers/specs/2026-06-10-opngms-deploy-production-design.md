# OPNGMS — Production Deployment (Dockerfile + Compose) — Design Spec

- **Date:** 2026-06-10
- **Status:** Approved (the user authorized proceeding with the deployment milestone)
- **Milestone:** Production deployment — the final non-hardware milestone (Phases 1–3 + 5 complete; Phase 4 partial)
- **Depends on:** the whole app (API, ARQ worker, frontend, migrations, two DB roles) in `main`
- **Enables:** running the full stack reproducibly in production

## 1. Context

The repo has a minimal dev `backend/Dockerfile` (worker-only, **missing WeasyPrint system deps** needed
by Phase 5 reporting) and a dev `backend/docker-compose.yml` (db/redis/worker only — no API, no frontend,
no migration runner). 5E reporting needs pango/cairo in the image. This milestone delivers a **production
Dockerfile per service + a production `docker-compose.prod.yml`** for the whole stack, with the security
model (the `opngms_app`/owner role split, RLS, secrets) preserved.

## 2. Architecture (production)

```
                       ┌──────────────┐
  browser ──HTTP:80──► │  frontend     │  nginx: serves the SPA + reverse-proxies /api → api:8000
                       │  (nginx)      │  (single origin → secure cookies + CSRF work, no CORS)
                       └──────┬────────┘
                              │ /api/*
                       ┌──────▼────────┐   DATABASE_URL (opngms_app, RLS)   ┌────────────┐
                       │  api (uvicorn) ├───────────────────────────────────►│ TimescaleDB │
                       └───────────────┘                                     │  (db)       │
                       ┌───────────────┐   ADMIN_DATABASE_URL (owner)        └─────▲──────┘
                       │ worker (arq)  ├─────────────────────────────────────────┘ │
                       └──────┬────────┘                                            │
                              │ broker                                    ┌─────────┴───┐
                       ┌──────▼────────┐                                  │ migrate     │ one-shot:
                       │  redis        │                                  │ (alembic)   │ upgrade head
                       └───────────────┘                                  └─────────────┘ (owner)
```

## 3. Design decisions

| Topic | Decision |
|-------|----------|
| Backend image | **One image** for api/worker/migrate (DRY): `python:3.12-slim` + WeasyPrint **system deps** (pango/cairo/gdk-pixbuf/libffi/shared-mime-info/fonts) + `pip install .` (non-editable) + a **non-root** user. Commands differ per service (uvicorn / arq / alembic). |
| Frontend image | **Multi-stage**: `node:20` build (`VITE_API_BASE` empty → relative `/api`) → **`nginx:alpine`** serving `dist` + an `nginx.conf` reverse-proxying `/api/` → `api:8000` (single origin for cookie auth + CSRF; SPA fallback to `index.html`). |
| Migrations | A **one-shot `migrate` service** runs `alembic upgrade head` as the **owner** (creates schema + the `opngms_app` role + RLS + grants via migration 0003). `api`/`worker` wait on `migrate: service_completed_successfully`. |
| Role split (preserved) | `api` → `DATABASE_URL` = `opngms_app` (non-superuser → RLS enforced). `worker` + `migrate` → `ADMIN_DATABASE_URL` = `opngms` owner (fleet-wide writes / DDL). |
| Secrets | `.env.example` (committed, placeholders + generation commands for `SESSION_SECRET`/`MASTER_KEY`); the real `.env` is **gitignored**. No secret baked into any image. `env_file: .env` on the services. |
| Health + lifecycle | Healthchecks: db (`pg_isready`), redis (`redis-cli ping`), api (`GET /healthz`). `depends_on` with `condition: service_healthy` / `service_completed_successfully`. `restart: unless-stopped` (except the one-shot `migrate`). Named volume for Postgres data. |
| Hardening | Non-root backend user; `.dockerignore` per context (no `.venv`/`node_modules`/`.env`/tests in the image); minimal apt layers. |

## 4. Files

| File | Responsibility |
|------|----------------|
| `backend/Dockerfile` | **Rewrite**: slim Python + WeasyPrint deps + non-root + `pip install .`; default CMD = api (uvicorn) |
| `backend/.dockerignore` | exclude `.venv`, `__pycache__`, `tests`, `.env`, etc. |
| `frontend/Dockerfile` | multi-stage node build → nginx |
| `frontend/nginx.conf` | SPA + `/api` reverse-proxy to `api:8000` |
| `frontend/.dockerignore` | exclude `node_modules`, `dist`, `.env` |
| `docker-compose.prod.yml` | the full stack (db, redis, migrate, api, worker, frontend) |
| `.env.example` | all required env vars + generation instructions |
| `.gitignore` | ensure `.env` is ignored |
| `README.md` | a "Production deployment" section |

## 5. Service contracts (`docker-compose.prod.yml`)

- **db**: `timescale/timescaledb:2.17.2-pg16`; `POSTGRES_USER/PASSWORD/DB` from env; named volume `opngms_pg`; healthcheck `pg_isready`.
- **redis**: `redis:7`; healthcheck `redis-cli ping`.
- **migrate**: backend image; `command: alembic upgrade head`; `ADMIN_DATABASE_URL`; `depends_on: db (healthy)`; `restart: "no"`.
- **api**: backend image; `command: uvicorn app.main:app --host 0.0.0.0 --port 8000`; `DATABASE_URL` (opngms_app) + `ADMIN_DATABASE_URL` + `REDIS_URL` + `SESSION_SECRET` + `MASTER_KEY`; `depends_on: migrate (completed) + db (healthy) + redis (healthy)`; healthcheck `GET /healthz`; `restart: unless-stopped`.
- **worker**: backend image; `command: arq app.worker.WorkerSettings`; owner DB + redis env; `depends_on: migrate (completed) + redis (healthy)`; `restart: unless-stopped`.
- **frontend**: frontend image; `ports: "80:80"`; `depends_on: api`; `restart: unless-stopped`.

## 6. Security & safety

- **Role split preserved**: the API runs as the non-superuser `opngms_app` → **RLS is enforced in prod**
  (the core multi-tenant guarantee). Only worker/migrate use the owner.
- **Secrets** never committed/baked: `.env.example` has placeholders; `MASTER_KEY` (Fernet) +
  `SESSION_SECRET` generated by the operator; `.env` gitignored. Changing the default
  `opngms_app`/Postgres passwords is documented.
- **Non-root** backend container; minimal image surface; `.dockerignore` keeps tests/secrets/venv out.
- **Single origin**: nginx proxies `/api` so the session cookie (Secure/SameSite) + CSRF header model
  works without CORS; no cross-origin exposure of the API.
- **WeasyPrint** `url_fetcher` (data-only) already blocks SSRF at the app layer; the image only adds the
  rendering libs.

## 7. Verification (Docker is available in this environment)
- `docker compose -f docker-compose.prod.yml config` parses + interpolates cleanly.
- `docker build backend/` and `docker build frontend/` succeed; in the backend image `python -c "import weasyprint"` works (system deps present).
- **Boot smoke test**: `docker compose -f docker-compose.prod.yml up -d` with a test `.env` → `migrate`
  completes (alembic head, app role created), `api` healthcheck goes healthy, `GET /healthz` → `{"status":"ok"}`,
  the frontend serves `index.html` and proxies `/api`. Tear down after.

## 8. Milestone breakdown (for the plan)
1. **Backend image**: rewrite `backend/Dockerfile` (WeasyPrint deps + non-root + `pip install .`) + `.dockerignore`; build it + confirm `import weasyprint`.
2. **Frontend image**: `frontend/Dockerfile` (multi-stage) + `nginx.conf` (SPA + `/api` proxy) + `.dockerignore`; build it.
3. **Compose + env**: `docker-compose.prod.yml` + `.env.example` + `.gitignore`; `docker compose config` clean.
4. **Boot smoke + docs**: bring the stack up with a test `.env`, verify migrate/api-healthy/`/healthz`/frontend, tear down; add the README deployment section.

## 9. Definition of "Done"
- `docker compose -f docker-compose.prod.yml up` brings up db + redis + runs migrations (as owner) + api
  (as `opngms_app`, RLS) + worker (owner) + frontend (nginx proxying `/api`); `GET /healthz` is healthy;
  the SPA loads and talks to the API same-origin.
- WeasyPrint renders in the image (reporting works); the role split + RLS are preserved; secrets are not
  committed/baked; the backend container runs non-root.
- `docker compose config` valid; both images build; the boot smoke test passes; README documents it.

## 10. Non-goals / deferred
- **TLS/HTTPS termination** (a real cert/load balancer is environment-specific; nginx is HTTP:80 here — a
  reverse TLS proxy is a deployment-site concern). Documented as the operator's responsibility.
- **Orchestration** (k8s/helm), autoscaling, log shipping, backups — out of scope (compose only).
- **CI image publishing** — out of scope.
