# Frontend TLS & flexible production deployment

**Status:** Design approved (2026-06-11). No app-code change — Docker/nginx/compose/docs only.
**Date:** 2026-06-11

## Problem

The prod `frontend` (nginx serving the SPA + reverse-proxying `/api`) publishes `80:80` to the host in
**plain HTTP**. The intended model is "TLS terminated at an external reverse proxy" (the nginx forwards
`X-Forwarded-Proto`, sends HSTS), but as shipped the container can be exposed directly over HTTP on the
internet — insecure, and it **breaks the `Secure` session/CSRF cookies** (browsers don't send Secure
cookies over HTTP on a real domain → login fails). The `api` is already safe (not host-published).

## Goal

The frontend must run **behind anything**, covering **every** TLS-termination + certificate-management
choice, expressed cleanly via a **base + overrides** docker-compose layout, with a thorough README
install guide for **all** modes.

## Three scenarios (base + two overrides)

### Base — `docker-compose.prod.yml` (revised) → Scenario 1: external TLS terminator
- The `frontend` stays HTTP but is published to **`${FRONTEND_BIND:-127.0.0.1}:${FRONTEND_HTTP_PORT:-8080}:80`**
  — **not internet-facing by default**; ready to sit behind any upstream TLS terminator (Cloudflare,
  AWS ALB/NLB, an existing nginx/Caddy/Traefik, a k8s ingress). The nginx already forwards
  `X-Forwarded-Proto` so `Secure` cookies + the real scheme survive the proxy chain.
- Operator points their edge proxy at `127.0.0.1:8080`. To bind all interfaces deliberately, set
  `FRONTEND_BIND=0.0.0.0` (documented as "only if something else terminates TLS in front").

### `docker-compose.tls.yml` (override) → Scenario 2: built-in TLS, bring-your-own cert
- Frontend publishes `${HTTPS_PORT:-443}:443` and `${HTTP_PORT:-80}:80` on `0.0.0.0`; sets
  `TLS_MODE=builtin`, `SERVER_NAME=${DOMAIN:-_}`; mounts `${CERT_DIR:-./certs}:/etc/nginx/certs:ro`.
- The nginx serves HTTPS on 443 from `/etc/nginx/certs/{fullchain.pem,privkey.pem}` and **redirects
  80→443**. If no cert is mounted at boot, the entrypoint generates a **self-signed** cert (so it
  starts) and logs a loud warning to mount a real one.
- For a real cert, the operator drops their `fullchain.pem`+`privkey.pem` (any CA, or a manually
  obtained Let's Encrypt cert) into `./certs`.

### `docker-compose.caddy.yml` (override) → Scenario 3a: automatic ACME via Caddy
- Adds a **`caddy`** service (image `caddy:2`) in front, publishing 80+443; it does **automatic
  HTTPS** (obtains + auto-renews Let's Encrypt certs) for `${DOMAIN}` using `${ACME_EMAIL}`, and
  reverse-proxies to `frontend:80` (which stays HTTP on the compose network, not host-published in
  this mode). A small `Caddyfile` is templated from the env. Caddy data (cert storage) persists in a
  named volume. Requires a public DNS name pointing at the host and ports 80/443 reachable.

### `docker-compose.traefik.yml` (override) → Scenario 3b: automatic ACME via Traefik
- Adds a **`traefik`** service (image `traefik:v3`) in front, publishing 80+443, with the standard
  Docker provider + an ACME (Let's Encrypt) certresolver (`--certificatesresolvers.le.acme.*`,
  HTTP-01 challenge, `${ACME_EMAIL}`, storage in a named volume). The `frontend` service gets Traefik
  **labels** (`traefik.enable=true`, a `Host(\`${DOMAIN}\`)` router on websecure with `tls.certresolver=le`,
  a Host router on web that redirects to https, `loadbalancer.server.port=80`) and stays HTTP on the
  compose network. Traefik is the same controller many users already run on **Docker and Kubernetes**;
  on k8s the SPA sits behind a Traefik (or any) Ingress — Scenario 1's HTTP-forwarding model already
  covers that (the Ingress terminates TLS and sets `X-Forwarded-Proto`). Traefik's Docker socket is
  mounted **read-only** (`/var/run/docker.sock:ro`) — documented as the standard Traefik trade-off.

## Frontend nginx — mode-aware via an entrypoint

- `frontend/docker-entrypoint.sh`: based on `TLS_MODE` (`off` default | `builtin`), render the active
  server config from a template into `/etc/nginx/conf.d/default.conf`, then `exec nginx`.
  - `off`: today's HTTP server block (listen 80; SPA + `/api` proxy + headers + `X-Forwarded-Proto`
    handling). Unchanged behavior — Scenario 1 / Caddy front it.
  - `builtin`: an HTTPS server block (listen 443 ssl; same SPA/`/api`/headers) + a port-80 server that
    301-redirects to `https://$host$request_uri`. Cert paths from env; if the cert files are missing,
    `openssl` generates a self-signed pair first (+ warning).
- The existing security headers (CSP, HSTS, nosniff, frame-deny, Referrer/Permissions-Policy) and the
  `X-Forwarded-Proto` map + the optional `real_ip` block are preserved in both modes (kept in shared
  template snippets).
- `frontend/Dockerfile`: add `openssl`, copy the entrypoint + templates, `ENTRYPOINT` it (CMD stays
  nginx). `npm ci --legacy-peer-deps` is unchanged.

## Env (`.env.example` additions, all documented)
`FRONTEND_BIND` (default `127.0.0.1`), `FRONTEND_HTTP_PORT` (default `8080`), `TLS_MODE`
(`off`|`builtin`), `DOMAIN`, `SERVER_NAME`, `ACME_EMAIL`, `CERT_DIR` (default `./certs`),
`HTTP_PORT`/`HTTPS_PORT`.

## README — "Deployment" rewritten as an install guide
A clear, copy-pasteable guide for each scenario:
1. **Behind your reverse proxy / load balancer** (base only) — the recommended production default;
   how to point an upstream proxy at `127.0.0.1:8080` and what headers it must set (`X-Forwarded-Proto
   https`).
2. **Self-contained HTTPS with your own certificate** (`-f docker-compose.prod.yml -f
   docker-compose.tls.yml`) — where to put `fullchain.pem`/`privkey.pem`, the self-signed fallback.
3. **Self-contained HTTPS with automatic Let's Encrypt** (`… -f docker-compose.caddy.yml`) — set
   `DOMAIN` + `ACME_EMAIL`, point DNS, done.
Plus a short matrix (which file(s) to use, who owns the cert, ports published) and the first-superadmin
step.

## Testing / verification
- `docker compose -f docker-compose.prod.yml [ -f <override> ] config` validates each combination
  (parse + interpolation) — run all three in CI/locally.
- Local smoke tests: build the frontend image; (a) `builtin` mode with the self-signed fallback →
  `curl -k https://localhost` returns the SPA and `http://localhost` 301s to https; (b) Caddy override
  with a localhost/internal name → reachable. (ACME against the real Let's Encrypt is NOT exercised in
  CI — documented.)
- Confirm `Secure` cookies now work: over HTTPS (builtin/Caddy) login completes; over the base behind
  a proxy that sets `X-Forwarded-Proto https`, the app issues Secure cookies that the browser returns.

## Out of scope
- Kubernetes manifests/Helm; Traefik variant (Caddy chosen for the simplest automatic-HTTPS story);
  mTLS to the device API (separate connector concern); the syslog mTLS milestone.
