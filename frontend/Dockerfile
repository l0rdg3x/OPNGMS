# OPNGMS frontend image — build the SPA, then serve it (+ reverse-proxy /api) with nginx.

# Stage 1: build the static SPA.
FROM node:20-slim AS build
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
RUN apk upgrade --no-cache
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
