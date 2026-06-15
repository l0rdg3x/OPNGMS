#!/bin/sh
# OPNGMS syslog-ng entrypoint — runs syslog-ng plus a CRL reload-watcher.
#
# syslog-ng caches the CRL at SSL_CTX init (verified on 4.5.0, 2026-06-15), so an updated
# /certs/crl/<hash>.r0 (written by the OPNGMS worker from the revoked-cert ledger) only takes effect
# after a reload. This script starts syslog-ng in the foreground and a background poll loop that, when
# the crl-dir checksum changes, runs `syslog-ng-ctl reload`. Bounded revocation latency: <= 30s poll.
#
# POSIX sh (the balabit image ships /bin/sh, sha256sum, syslog-ng-ctl). SIGTERM/INT are forwarded so
# `docker stop` shuts syslog-ng down cleanly.
set -eu

CRL_GLOB="/certs/crl/*.r0"

# Checksum of the current CRL set ("" when no CRL file exists yet — handled so the loop never errors).
crl_sum() {
    # Word-splitting on the glob is intentional; with no match, sha256sum gets no args and prints
    # nothing (stderr suppressed), so the function yields "".
    # shellcheck disable=SC2086
    sha256sum $CRL_GLOB 2>/dev/null || true
}

# Start syslog-ng in the foreground, backgrounded so we can also run the watcher + trap signals.
syslog-ng --no-caps -F &
SNG=$!

# Clean shutdown on docker stop: ask syslog-ng to stop, then kill the process if still running.
trap 'syslog-ng-ctl stop 2>/dev/null || true; kill "$SNG" 2>/dev/null || true' TERM INT

# CRL reload-watcher: reload syslog-ng whenever the crl-dir checksum changes after the first reading.
(
    last="$(crl_sum)"
    while true; do
        sleep 30
        cur="$(crl_sum)"
        if [ "$cur" != "$last" ]; then
            echo "[entrypoint] CRL changed; reloading syslog-ng"
            syslog-ng-ctl reload 2>/dev/null || echo "[entrypoint] syslog-ng-ctl reload failed"
            last="$cur"
        fi
    done
) &

# Wait on syslog-ng; its exit becomes the container's exit (the watcher is a daemon child).
wait "$SNG"
