#!/bin/sh
# Render the active nginx config from TLS_MODE, at container start. Runs from the stock nginx image's
# /docker-entrypoint.d/ (after 20-envsubst, before nginx starts). Only ${SERVER_NAME} is substituted —
# nginx runtime variables ($host, $uri, $remote_addr, ...) are passed through untouched.
set -eu

TLS_MODE="${TLS_MODE:-off}"
SERVER_NAME="${SERVER_NAME:-_}"
export SERVER_NAME
SRC=/etc/nginx/opngms
OUT=/etc/nginx/conf.d/default.conf
CERTS=/etc/nginx/certs

if [ "$TLS_MODE" = "builtin" ]; then
    if [ ! -s "$CERTS/fullchain.pem" ] || [ ! -s "$CERTS/privkey.pem" ]; then
        echo "[opngms] WARNING: TLS_MODE=builtin but no certificate at $CERTS/{fullchain,privkey}.pem." >&2
        echo "[opngms]          Generating a SELF-SIGNED certificate so the server starts." >&2
        echo "[opngms]          Mount a real cert at \$CERT_DIR for production (see the README)." >&2
        mkdir -p "$CERTS"
        openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
            -keyout "$CERTS/privkey.pem" -out "$CERTS/fullchain.pem" \
            -subj "/CN=${SERVER_NAME}" >/dev/null 2>&1
    fi
    envsubst '${SERVER_NAME}' < "$SRC/default.builtin.conf.template" > "$OUT"
    echo "[opngms] TLS_MODE=builtin — serving HTTPS on :443 (80 redirects to 443), server_name=${SERVER_NAME}"
else
    envsubst '${SERVER_NAME}' < "$SRC/default.off.conf.template" > "$OUT"
    echo "[opngms] TLS_MODE=off — serving HTTP on :80 (terminate TLS upstream), server_name=${SERVER_NAME}"
fi
