"""TLS certificate fingerprint pinning for self-signed OPNsense devices.

When an operator pins a SHA-256 fingerprint, the connector verifies the device's leaf certificate
matches it BEFORE sending credentials (MITM-resistant). CERT_NONE is used ONLY to retrieve the peer
certificate for the fingerprint comparison — the comparison itself is the verification.
"""
import asyncio
import contextlib
import hashlib
import secrets
import ssl


class PinMismatchError(Exception):
    """The peer certificate fingerprint did not match the pinned value."""


def normalize_fingerprint(value: str) -> str:
    v = value.strip().lower()
    if v.startswith("sha256:"):
        v = v[len("sha256:"):]
    return v.replace(":", "").replace(" ", "")


async def peer_fingerprint(host: str, ip: str, port: int, *, timeout: float) -> str:
    """Connect to the (SSRF-pinned) IP with SNI=host and return the leaf cert's SHA-256 hex."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # retrieve-only; the fingerprint match is the actual check
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host=ip, port=port, ssl=ctx, server_hostname=host),
        timeout=timeout,
    )
    try:
        ssl_obj = writer.get_extra_info("ssl_object")
        der = ssl_obj.getpeercert(binary_form=True) if ssl_obj else None
    finally:
        writer.close()
        with contextlib.suppress(Exception):  # best-effort close
            await writer.wait_closed()
    if not der:
        raise ssl.SSLError("no peer certificate")
    return hashlib.sha256(der).hexdigest()


async def verify_pinned(host: str, ip: str, port: int, expected: str, *, timeout: float) -> None:
    """Raise PinMismatchError if the peer cert's SHA-256 != the pinned fingerprint."""
    actual = await peer_fingerprint(host, ip, port, timeout=timeout)
    # Constant-time compare: the match gates whether device credentials are sent, so don't leak
    # how many leading bytes matched via a short-circuiting `!=`.
    if not secrets.compare_digest(actual, normalize_fingerprint(expected)):
        raise PinMismatchError()
