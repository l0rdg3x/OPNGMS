# OPNGMS — Production Deployment — Implementation Plan

> Infra milestone: the deliverables are Docker/compose config validated by real `docker build` + a boot smoke test (Docker is available). Executed directly with Docker validation, then a holistic review.

**Goal:** A production Dockerfile per service + a `docker-compose.prod.yml` that brings up the whole stack (db, redis, migrate, api, worker, frontend) reproducibly, with the `opngms_app`/owner role split + RLS + secrets model preserved and WeasyPrint working in the image.

**Spec:** `docs/superpowers/specs/2026-06-10-opngms-deploy-production-design.md`.

---

## Task 1: Backend production image
- Rewrite `backend/Dockerfile`: `python:3.12-slim`; `apt-get install` WeasyPrint runtime deps (`libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 libffi8 shared-mime-info fonts-dejavu-core`) + build deps for any wheels; copy `pyproject.toml app migrations alembic.ini`; `pip install .` (non-editable); create + switch to a non-root user; default `CMD` = api (`uvicorn app.main:app --host 0.0.0.0 --port 8000`).
- `backend/.dockerignore` (exclude `.venv __pycache__ tests *.pyc .env .pytest_cache`).
- **Validate:** `docker build -t opngms-backend backend/` succeeds; `docker run --rm opngms-backend python -c "import weasyprint; print(weasyprint.__version__)"` works.

## Task 2: Frontend production image
- `frontend/Dockerfile` multi-stage: stage 1 `node:20` → `npm ci --legacy-peer-deps` → `npm run build` (with `VITE_API_BASE=""` so the SPA uses relative `/api`); stage 2 `nginx:alpine` → copy `dist` to `/usr/share/nginx/html` + copy `nginx.conf`.
- `frontend/nginx.conf`: `server { listen 80; root /usr/share/nginx/html; location /api/ { proxy_pass http://api:8000; proxy_set_header Host $host; proxy_set_header X-Forwarded-For $remote_addr; } location / { try_files $uri $uri/ /index.html; } }`.
- `frontend/.dockerignore` (`node_modules dist .env`).
- **Validate:** `docker build -t opngms-frontend frontend/` succeeds.

## Task 3: Compose + env
- `docker-compose.prod.yml` (root): services db / redis / migrate / api / worker / frontend per the spec's service contracts; `env_file: .env`; healthchecks; `depends_on` conditions; named volume; `restart` policies.
- `.env.example` (root): `POSTGRES_USER/PASSWORD/DB`, `DATABASE_URL` (opngms_app), `ADMIN_DATABASE_URL` (owner), `REDIS_URL`, `SESSION_SECRET`, `MASTER_KEY` — with generation commands in comments.
- Ensure `.env` is in root `.gitignore`.
- **Validate:** `docker compose -f docker-compose.prod.yml config` parses cleanly (with a test `.env`).

## Task 4: Boot smoke + docs
- Create a throwaway `.env` (test secrets), `docker compose -f docker-compose.prod.yml up -d --build`; wait for `migrate` to complete + `api` healthy; `curl http://localhost:8000/healthz` (or via the api container) → `{"status":"ok"}`; confirm the frontend serves `index.html` and `/api/...` proxies; `docker compose down -v`; remove the throwaway `.env`.
- Add a **Production deployment** section to `README.md` (env setup, `docker compose -f docker-compose.prod.yml up -d --build`, first-superadmin `POST /api/setup`, TLS note).

## Definition of "Done"
Per the spec §9: the stack boots (migrate→api/worker/frontend), `/healthz` healthy, WeasyPrint renders, role split + RLS preserved, secrets not committed/baked, backend non-root, compose valid, README documents it.
