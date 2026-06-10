"""Tests for the TLS fingerprint pinning helper.

Uses a real asyncio TLS server (self-signed cert built with cryptography)
so that peer_fingerprint / verify_pinned exercise an actual TLS handshake.
"""
import asyncio
import hashlib
import ipaddress
import ssl
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.connectors.opnsense.tls_pinning import (
    PinMismatchError,
    normalize_fingerprint,
    peer_fingerprint,
    verify_pinned,
)


# ---------------------------------------------------------------------------
# normalize_fingerprint — pure unit tests, no I/O
# ---------------------------------------------------------------------------

def test_normalize_fingerprint_colons_and_case():
    assert normalize_fingerprint("AA:BB:cc") == "aabbcc"


def test_normalize_fingerprint_strips_sha256_prefix():
    assert normalize_fingerprint("sha256:aabbcc") == "aabbcc"


def test_normalize_fingerprint_strips_whitespace():
    assert normalize_fingerprint("  AABBCC  ") == "aabbcc"


def test_normalize_fingerprint_combined():
    # colon-formatted, uppercase, sha256 prefix, leading space
    assert normalize_fingerprint(" SHA256:AA:BB:CC") == "aabbcc"


# ---------------------------------------------------------------------------
# Real-TLS-server fixture
# ---------------------------------------------------------------------------

@pytest.fixture
async def tls_server():
    """Build a self-signed cert, start a TLS server on 127.0.0.1:0, yield (port, expected_hex)."""
    # --- generate EC key + self-signed cert ---
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(tz=timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    # expected fingerprint from DER
    der = cert.public_bytes(serialization.Encoding.DER)
    expected_hex = hashlib.sha256(der).hexdigest()

    # write cert + key PEM to temp files
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )

    with tempfile.NamedTemporaryFile(suffix=".crt", delete=False) as cf:
        cf.write(cert_pem)
        cert_path = cf.name
    with tempfile.NamedTemporaryFile(suffix=".key", delete=False) as kf:
        kf.write(key_pem)
        key_path = kf.name

    # TLS server context
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)

    async def handler(reader, writer):
        writer.close()

    server = await asyncio.start_server(
        handler, host="127.0.0.1", port=0, ssl=server_ctx
    )
    port = server.sockets[0].getsockname()[1]

    yield port, expected_hex

    server.close()
    await server.wait_closed()

    import os
    os.unlink(cert_path)
    os.unlink(key_path)


# ---------------------------------------------------------------------------
# Real-TLS-server tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_peer_fingerprint_matches(tls_server):
    port, expected_hex = tls_server
    result = await peer_fingerprint("localhost", "127.0.0.1", port, timeout=5)
    assert result == expected_hex


@pytest.mark.asyncio
async def test_verify_pinned_ok(tls_server):
    port, expected_hex = tls_server
    # exact hex — must not raise
    await verify_pinned("localhost", "127.0.0.1", port, expected_hex, timeout=5)

    # colon-formatted uppercase variant — must also not raise
    colon_upper = ":".join(expected_hex[i:i+2].upper() for i in range(0, len(expected_hex), 2))
    await verify_pinned("localhost", "127.0.0.1", port, colon_upper, timeout=5)


@pytest.mark.asyncio
async def test_verify_pinned_mismatch(tls_server):
    port, _ = tls_server
    with pytest.raises(PinMismatchError):
        await verify_pinned("localhost", "127.0.0.1", port, "00" * 32, timeout=5)
