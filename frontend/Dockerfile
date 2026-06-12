# OPNGMS frontend image — build the SPA, then serve it (+ reverse-proxy /api) with nginx.

# Stage 1: build the static SPA. Node 24 is the current Active LTS (Node 20 is end-of-life).
FROM node:24-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --legacy-peer-deps
COPY . .
# Empty VITE_API_BASE -> the SPA calls relative /api, which nginx proxies to the API (same origin,
# so the session cookie + CSRF header model works without CORS).
ENV VITE_API_BASE=""
RUN npm run build

# Stage 2: serve with nginx.
FROM nginx:alpine
# Apply Alpine security updates to the base image at build time (e.g. the libxml2 -r1 fix for
# CVE-2026-6732) so the image stays patched on every rebuild without chasing individual CVEs.
# openssl: the self-signed fallback for TLS_MODE=builtin. gettext: envsubst (also used by the stock
# image's template step) — installed explicitly so the render script never depends on it implicitly.
RUN apk upgrade --no-cache && apk add --no-cache openssl gettext tzdata
COPY --from=build /app/dist /usr/share/nginx/html
# Mode-aware config: a shared server body + two templates (TLS off / built-in) rendered at startup by
# the render script from TLS_MODE. Templates live OUTSIDE /etc/nginx/templates so the stock image's
# envsubst step does not also process them. Drop the stock welcome config.
RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx/snippets/ /etc/nginx/snippets/
COPY nginx/default.off.conf.template nginx/default.builtin.conf.template /etc/nginx/opngms/
COPY nginx/40-opngms-render.sh /docker-entrypoint.d/40-opngms-render.sh
RUN chmod +x /docker-entrypoint.d/40-opngms-render.sh
# TLS off by default (terminate TLS upstream). Override (build-in TLS / Caddy / Traefik) set this.
ENV TLS_MODE=off
EXPOSE 80 443
