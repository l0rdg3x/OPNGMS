"""Host-level admin CLI (break-glass). Usage: python -m app.cli <command> [options].

Commands:
  mfa-reset       Clear a user's MFA + recovery codes (recovery path for locked-out superadmin).
  syslog-bootstrap Ensure the syslog CA, write receiver cert files, and apply the OpenSearch index
                  template. Log retention is now owned by the OPNGMS worker (per-tenant), so this
                  command also removes any pre-existing global ISM retention policy.

Connects via ADMIN_DATABASE_URL (owner role) for privileged DB operations."""
import argparse
import asyncio
import json
from pathlib import Path

import httpx
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core import crypto
from app.core.config import get_settings
from app.core.db import make_engine
from app.models.audit import AuditLog
from app.models.user import User
from app.models.user_mfa import UserMfa
from app.models.user_recovery_code import UserRecoveryCode
from app.services.log_forwarding import SyslogCaService
from app.services.syslog_ca import issue_server_cert
from app.services.syslog_crl import refresh_syslog_crl

# Path to deploy/opensearch/ relative to this file: backend/app/cli.py -> repo root is parents[2].
_DEPLOY_OPENSEARCH = Path(__file__).resolve().parents[2] / "deploy" / "opensearch"


async def reset_user_mfa(email: str, *, engine: AsyncEngine | None = None) -> int:
    """Clear a user's MFA + recovery codes. Returns the number of users affected (0 or 1)."""
    if engine is None:
        dsn = get_settings().admin_database_url
        if not dsn:
            raise RuntimeError("ADMIN_DATABASE_URL is not configured")
        eng = make_engine(dsn)
    else:
        eng = engine
    factory = async_sessionmaker(eng, expire_on_commit=False)
    async with factory() as s:
        user = (await s.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            if engine is None:
                await eng.dispose()
            return 0
        await s.execute(delete(UserRecoveryCode).where(UserRecoveryCode.user_id == user.id))
        await s.execute(delete(UserMfa).where(UserMfa.user_id == user.id))
        # Break-glass is an out-of-band privileged action: record it so the reset is auditable.
        s.add(
            AuditLog(
                actor_user_id=None,
                tenant_id=None,
                action="mfa.cli_reset",
                target_type="user",
                target_id=str(user.id),
                ip=None,
                details={"email": email},
            )
        )
        await s.commit()
    if engine is None:
        await eng.dispose()
    return 1


async def run_syslog_bootstrap(cert_dir: Path, *, force: bool, engine: AsyncEngine | None = None) -> None:
    """Ensure the syslog CA, write receiver cert files to cert_dir, and apply OpenSearch config."""
    settings = get_settings()

    # --- 1. Open a DB session (owner role via ADMIN_DATABASE_URL, same pattern as reset_user_mfa). ---
    if engine is None:
        dsn = settings.admin_database_url
        if not dsn:
            raise RuntimeError("ADMIN_DATABASE_URL is not configured")
        eng = make_engine(dsn)
    else:
        eng = engine
    factory = async_sessionmaker(eng, expire_on_commit=False)
    async with factory() as session:
        svc = SyslogCaService(session)
        ca = await svc.ensure_ca()
        await session.commit()
        # Read the (encrypted) CA private key to sign the receiver server cert via the same SECURITY
        # DEFINER accessor the app role uses, to keep one read path. (Bootstrap runs as the DB owner
        # via ADMIN_DATABASE_URL, which *could* SELECT syslog_ca_key directly — but only because it is
        # owner-context code; never read the key table directly from an app-role path.)
        key_enc = (await session.execute(text("SELECT opngms_syslog_ca_key()"))).scalar_one()

    # --- 2. Issue the receiver server cert. ---
    key_pem = crypto.decrypt_bytes(bytes(key_enc))
    server_cert_pem, server_key_pem = issue_server_cert(
        ca.cert_pem.encode(), key_pem, hostname=settings.syslog_receiver_host
    )

    # --- 3. Write cert files to cert_dir. ---
    cert_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "CA.pem": ca.cert_pem.encode(),
        "server.pem": server_cert_pem,
        "server.key": server_key_pem,
    }
    for name, data in files.items():
        dest = cert_dir / name
        if dest.exists() and not force:
            print(f"  [skip] {dest} already exists (use --force to overwrite)")
        else:
            dest.write_bytes(data)
            # Private keys must not be world/group-readable.
            if dest.name.endswith(".key"):
                dest.chmod(0o600)
            print(f"  [write] {dest}")

    # --- 3b. Write the initial CRL (possibly empty) so crl-dir() has a valid hash-named file at first
    # start — syslog-ng's crl-dir() must not point at an empty dir. Owner session (same engine). ---
    async with factory() as session:
        n = await refresh_syslog_crl(session, str(cert_dir))
    print(f"  [write] initial syslog CRL ({n} revoked entries)")

    if engine is None:
        await eng.dispose()

    # --- 4. Apply the OpenSearch index template; REMOVE the global ISM retention policy. ---
    # Retention is now per-tenant and owned by the worker's purge_log_lake cron (it deletes each tenant's
    # over-age indices at the tenant's effective age). A global ISM policy would keep deleting whole
    # opngms-logs-* indices at the GLOBAL age, violating any longer per-tenant override — so we detach it
    # from existing indices and delete the policy. Both removals are best-effort (the lake is optional /
    # may be a fresh cluster with no policy). The index template (mappings) is still applied.
    index_template = json.loads((_DEPLOY_OPENSEARCH / "index-template.json").read_text())

    with httpx.Client() as client:
        url_tpl = f"{settings.opensearch_url}/_index_template/opngms-logs"
        resp = client.put(url_tpl, json=index_template)
        resp.raise_for_status()
        print(f"  [opensearch] index template applied: {resp.status_code}")

        # Detach the policy from any indices it is currently managing (ignore errors — none may exist).
        url_remove = f"{settings.opensearch_url}/_plugins/_ism/remove/opngms-logs-*"
        try:
            resp = client.post(url_remove)
            resp.raise_for_status()  # don't print success on a 5xx — surface it as skipped instead
            print(f"  [opensearch] ISM detach from opngms-logs-*: {resp.status_code}")
        except httpx.HTTPError as exc:
            print(f"  [opensearch] ISM detach skipped ({exc.__class__.__name__})")

        # Delete the global retention policy itself (404 = already absent).
        url_ism = f"{settings.opensearch_url}/_plugins/_ism/policies/opngms-logs-retention"
        try:
            resp = client.delete(url_ism)
            if resp.status_code == 404:
                print("  [opensearch] ISM policy opngms-logs-retention: already absent")
            else:
                resp.raise_for_status()
                print(f"  [opensearch] ISM policy opngms-logs-retention removed: {resp.status_code}")
        except httpx.HTTPError as exc:
            print(f"  [opensearch] ISM policy removal skipped ({exc.__class__.__name__})")
        print("  [opensearch] log retention is now owned by the OPNGMS worker (per-tenant purge_log_lake)")


def main() -> None:
    p = argparse.ArgumentParser(prog="app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("mfa-reset", help="Clear a user's MFA + recovery codes (break-glass).")
    r.add_argument("--email", required=True)

    b = sub.add_parser(
        "syslog-bootstrap",
        help="Ensure syslog CA, write receiver cert files, apply the OpenSearch index template, and "
        "remove the legacy global ISM retention policy (the worker now owns per-tenant retention).",
    )
    b.add_argument("--cert-dir", default="/certs", help="Directory to write CA.pem, server.pem, server.key.")
    b.add_argument("--force", action="store_true", help="Overwrite existing cert files.")

    args = p.parse_args()
    if args.cmd == "mfa-reset":
        n = asyncio.run(reset_user_mfa(args.email))
        print(f"MFA reset for {args.email}: {n} user(s) affected")
    elif args.cmd == "syslog-bootstrap":
        asyncio.run(run_syslog_bootstrap(Path(args.cert_dir), force=args.force))


if __name__ == "__main__":
    main()
