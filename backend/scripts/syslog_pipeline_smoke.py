"""Scripted mTLS → OpenSearch syslog pipeline smoke check.

Prerequisites
-------------
The log-lake compose stack must be running before executing this script::

    docker compose -f docker-compose.prod.yml -f docker-compose.logs.yml up -d

How to run
----------
From the repository root::

    cd backend && .venv/bin/python scripts/syslog_pipeline_smoke.py

Environment variables (all optional, shown with defaults)::

    SYSLOG_HOST=localhost
    SYSLOG_TLS_PORT=6514
    OPENSEARCH_URL=http://localhost:9200

OpenSearch note
---------------
When run from the **host machine**, OpenSearch is not exposed by default (it listens
on the internal Docker network).  Either temporarily publish port 9200 in
docker-compose.logs.yml, or run this script from inside the Docker network
(e.g. ``docker compose exec syslog-receiver python ...``).  The script will simply
time out waiting for the document if OpenSearch is unreachable.

TLS hostname check
------------------
The script intentionally sets ``check_hostname=False`` on the outgoing TLS
connection.  The syslog receiver's server certificate SAN is set to its
configured hostname (e.g. the container name or a private IP), which will not
match ``localhost`` when running from the host.  The CA is still verified
(``verify_mode=ssl.CERT_REQUIRED``), so the connection is authenticated; only
the hostname comparison is skipped.
"""

from __future__ import annotations

import asyncio
import os
import socket
import ssl
import sys
import tempfile
import time

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import crypto
from app.core.config import get_settings
from app.core.db import make_engine
from app.services.log_forwarding import SyslogCaService
from app.services.syslog_ca import issue_device_cert

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SYSLOG_HOST = os.environ.get("SYSLOG_HOST", "localhost")
SYSLOG_TLS_PORT = int(os.environ.get("SYSLOG_TLS_PORT", "6514"))
OPENSEARCH_URL = os.environ.get("OPENSEARCH_URL", "http://localhost:9200")

SMOKE_TENANT = "tenant-smoke"
SMOKE_DEVICE = "dev-smoke"

# RFC 5424 test message
SYSLOG_LINE = (
    "<134>1 2026-06-12T00:00:00Z dev-smoke filterlog - - - smoke test message\n"
)

POLL_TIMEOUT_S = 20
POLL_INTERVAL_S = 2

# ---------------------------------------------------------------------------
# Step 1: load CA from DB
# ---------------------------------------------------------------------------


async def _load_ca() -> tuple[bytes, bytes]:
    """Return (ca_cert_pem, ca_key_pem) from the OPNGMS database."""
    settings = get_settings()
    db_url = settings.admin_database_url or settings.database_url
    engine = make_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session, session.begin():
            ca_row = await SyslogCaService(session).ensure_ca()
            ca_cert_pem: bytes = ca_row.cert_pem.encode()
            ca_key_pem: bytes = crypto.decrypt_bytes(bytes(ca_row.key_enc))
    finally:
        await engine.dispose()
    return ca_cert_pem, ca_key_pem


def load_ca() -> tuple[bytes, bytes]:
    return asyncio.run(_load_ca())


# ---------------------------------------------------------------------------
# Step 2: issue a throwaway client cert
# ---------------------------------------------------------------------------


def issue_smoke_cert(
    ca_cert_pem: bytes, ca_key_pem: bytes
) -> tuple[bytes, bytes]:
    return issue_device_cert(
        ca_cert_pem,
        ca_key_pem,
        tenant_id=SMOKE_TENANT,
        device_id=SMOKE_DEVICE,
    )


# ---------------------------------------------------------------------------
# Step 3: ship one syslog line over mTLS
# ---------------------------------------------------------------------------


def send_syslog(
    ca_cert_file: str,
    client_cert_file: str,
    client_key_file: str,
) -> None:
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    ctx.load_verify_locations(ca_cert_file)
    ctx.load_cert_chain(certfile=client_cert_file, keyfile=client_key_file)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2  # never negotiate down to TLS 1.0/1.1
    # Intentional: server SAN may not match 'localhost' when running from host.
    # We still require + verify the server cert against our CA (CERT_REQUIRED).
    ctx.check_hostname = False  # noqa: S501
    ctx.verify_mode = ssl.CERT_REQUIRED

    print(f"  Connecting to {SYSLOG_HOST}:{SYSLOG_TLS_PORT} …")
    with (
        socket.create_connection((SYSLOG_HOST, SYSLOG_TLS_PORT), timeout=10) as raw,
        ctx.wrap_socket(raw, server_hostname=SYSLOG_HOST) as tls,
    ):
        tls.sendall(SYSLOG_LINE.encode())
    print("  Syslog line sent.")


# ---------------------------------------------------------------------------
# Step 4: poll OpenSearch for the document
# ---------------------------------------------------------------------------


def poll_opensearch() -> bool:
    url = (
        f"{OPENSEARCH_URL.rstrip('/')}/opngms-logs-*/_search"
        f"?q=device_id:{SMOKE_DEVICE}"
    )
    deadline = time.monotonic() + POLL_TIMEOUT_S
    print(f"  Polling OpenSearch ({OPENSEARCH_URL}) for up to {POLL_TIMEOUT_S}s …")
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                hits = data.get("hits", {}).get("hits", [])
                for hit in hits:
                    src = hit.get("_source", {})
                    if (
                        src.get("tenant_id") == SMOKE_TENANT
                        and src.get("device_id") == SMOKE_DEVICE
                    ):
                        return True
        except httpx.RequestError:
            pass  # connection refused / network error — keep polling
        time.sleep(POLL_INTERVAL_S)
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=== OPNGMS syslog pipeline smoke check ===")

    # -- Step 1: CA
    print("[1/4] Loading CA from database …")
    try:
        ca_cert_pem, ca_key_pem = load_ca()
    except Exception as exc:
        print(f"  ERROR loading CA: {exc}")
        return 1
    print("  CA loaded.")

    # -- Step 2: issue client cert
    print("[2/4] Issuing throwaway client cert …")
    try:
        client_cert_pem, client_key_pem = issue_smoke_cert(ca_cert_pem, ca_key_pem)
    except Exception as exc:
        print(f"  ERROR issuing client cert: {exc}")
        return 1
    print("  Client cert issued.")

    # -- Step 3 & 4 — use temp files, always cleaned up
    tmp_dir = tempfile.mkdtemp(prefix="opngms_smoke_")
    ca_file = f"{tmp_dir}/ca.pem"
    cert_file = f"{tmp_dir}/client.pem"
    key_file = f"{tmp_dir}/client.key"

    import shutil  # noqa: PLC0415  (local import keeps stdlib at top conceptually)

    try:
        with open(ca_file, "wb") as f:
            f.write(ca_cert_pem)
        with open(cert_file, "wb") as f:
            f.write(client_cert_pem)
        with open(key_file, "wb") as f:
            f.write(client_key_pem)

        # -- Step 3: send syslog line
        print("[3/4] Sending mTLS syslog line …")
        try:
            send_syslog(ca_file, cert_file, key_file)
        except Exception as exc:
            print(f"  ERROR sending syslog: {exc}")
            return 1

        # -- Step 4: poll OpenSearch
        print("[4/4] Waiting for document in OpenSearch …")
        found = poll_opensearch()

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if found:
        print("\nRESULT: PASS — document found in OpenSearch with correct tenant/device tags.")
        return 0
    else:
        print(
            "\nRESULT: FAIL — document NOT found in OpenSearch within "
            f"{POLL_TIMEOUT_S}s.  Check the syslog-ng → OpenSearch pipeline."
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
