"""Owner-side CRL refresh for the log pipeline (PR2 — CRL hard-revoke).

Builds a single CA-signed CRL covering every revoked device cert (across all tenants) from the
``revoked_syslog_certs`` ledger and writes it, hash-named ``<issuer_subject_hash>.r0``, onto the shared
cert volume's ``crl/`` directory. syslog-ng's ``crl-dir()`` (with ``peer-verify(required-trusted)``)
then rejects a revoked client cert at the TLS handshake.

Callable from the worker cron (``refresh_syslog_crl_job``) and the bootstrap CLI. Runs as the DB owner
(RLS-exempt) so it sees every tenant's ledger rows in one read. No-ops gracefully (returns ``"skipped"``)
when the cert volume isn't mounted (core-only deploy) or the CA hasn't been bootstrapped yet — the same
opt-in-degrades pattern as ``purge_log_lake``.

NEVER logs the CA private key or the CRL contents.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.services.log_forwarding import SyslogCaService
from app.services.syslog_ca import build_crl

logger = logging.getLogger(__name__)


def _crl_hash_name(crl_pem: bytes) -> str:
    """OpenSSL's hash-dir lookup name for a CRL: ``openssl crl -hash -noout`` (the 8-char issuer hash).

    syslog-ng (OpenSSL 3) looks up CRLs in ``crl-dir()`` by ``<issuer_subject_hash>.r0``; we compute the
    hash with the CLI so it matches OpenSSL's own algorithm exactly (rather than re-deriving it).
    """
    with tempfile.NamedTemporaryFile(suffix=".pem") as tmp:
        tmp.write(crl_pem)
        tmp.flush()
        os.fsync(tmp.fileno())  # ensure the full CRL is on disk before openssl reads the path
        out = subprocess.run(
            ["openssl", "crl", "-hash", "-noout", "-in", tmp.name],
            capture_output=True, text=True, check=True,
        )
    return out.stdout.strip()


async def refresh_syslog_crl(session: AsyncSession, cert_dir: str | None) -> int | str:
    """Rebuild the syslog CRL from the ledger and write it onto the cert volume.

    Returns the number of revoked entries written, or ``"skipped"`` for the degraded paths:
    - ``cert_dir`` falsy / not a directory → the cert volume isn't mounted (core-only deploy);
    - no CA row → the owner-side bootstrap hasn't run yet.

    Owner session (RLS-exempt — reads every tenant's ``revoked_syslog_certs``). The write is atomic
    (temp file + ``os.replace``) so syslog-ng's reload-watcher never observes a half-written CRL.
    """
    if not cert_dir or not os.path.isdir(cert_dir):
        logger.info("syslog cert dir not present; skipping CRL refresh")
        return "skipped"

    ca = await SyslogCaService(session).get()
    if ca is None:
        logger.info("syslog CA not bootstrapped; skipping CRL refresh")
        return "skipped"

    key_enc = (await session.execute(text("SELECT opngms_syslog_ca_key()"))).scalar_one()
    key_pem = crypto.decrypt_bytes(bytes(key_enc))

    # Owner read across all tenants: each ledger row's hex serial → int, with its revocation date.
    rows = (await session.execute(
        text("SELECT serial, revoked_at FROM revoked_syslog_certs")
    )).all()
    # Dedupe by serial (RFC 5280 §5.1: a CRL MUST NOT list a serial twice — a re-revocation or a
    # racing double-revoke could otherwise produce a CRL that strict OpenSSL/syslog-ng rejects), keeping
    # the earliest revocation date. A malformed serial is skipped (logged) rather than aborting the whole
    # refresh, which would leave the OLD CRL stale and a revoked cert still accepted.
    by_serial: dict[int, datetime] = {}
    skipped = 0
    for serial_hex, revoked_at in rows:
        try:
            serial_int = int(serial_hex, 16)
        except (ValueError, TypeError):
            skipped += 1
            continue
        when = revoked_at or datetime.now(UTC)
        if serial_int not in by_serial or when < by_serial[serial_int]:
            by_serial[serial_int] = when
    if skipped:
        logger.warning("CRL refresh skipped %d malformed revoked-cert serial(s)", skipped)
    revoked: list[tuple[int, datetime]] = sorted(by_serial.items())

    crl_pem = build_crl(ca.cert_pem.encode(), key_pem, revoked)
    hash_name = _crl_hash_name(crl_pem)

    crl_dir = os.path.join(cert_dir, "crl")
    os.makedirs(crl_dir, exist_ok=True)
    target = os.path.join(crl_dir, f"{hash_name}.r0")
    # Atomic replace: write to a temp file in the same dir, then rename over the target.
    fd, tmp_path = tempfile.mkstemp(dir=crl_dir, suffix=".r0.tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(crl_pem)
        os.replace(tmp_path, target)
    except BaseException:
        # Clean up the temp file on any failure so we don't litter the crl-dir.
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    return len(revoked)
