"""Host-level admin CLI (break-glass). Usage: python -m app.cli <command> [options].

Commands:
  mfa-reset       Clear a user's MFA + recovery codes (recovery path for locked-out superadmin).
  syslog-bootstrap Ensure the syslog CA, write receiver cert files, and apply OpenSearch index
                  template + ISM retention policy.

Connects via ADMIN_DATABASE_URL (owner role) for privileged DB operations."""
import argparse
import asyncio
import json
from pathlib import Path

import httpx
from sqlalchemy import delete, select
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

    if engine is None:
        await eng.dispose()

    # --- 2. Issue the receiver server cert. ---
    key_pem = crypto.decrypt_bytes(bytes(ca.key_enc))
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

    # --- 4. Apply OpenSearch index template + ISM retention policy (plain HTTP, no auth). ---
    index_template = json.loads((_DEPLOY_OPENSEARCH / "index-template.json").read_text())
    ism_raw = (_DEPLOY_OPENSEARCH / "ism-policy.json").read_text().replace(
        "{{RETENTION_DAYS}}", str(settings.log_retention_days)
    )
    ism_policy = json.loads(ism_raw)

    with httpx.Client() as client:
        url_tpl = f"{settings.opensearch_url}/_index_template/opngms-logs"
        resp = client.put(url_tpl, json=index_template)
        resp.raise_for_status()
        print(f"  [opensearch] index template applied: {resp.status_code}")

        url_ism = f"{settings.opensearch_url}/_plugins/_ism/policies/opngms-logs-retention"
        resp = client.put(url_ism, json=ism_policy)
        # A pre-existing policy returns 409 (PUT without seq_no/primary_term); treat as already-applied.
        if resp.status_code != 409:
            resp.raise_for_status()
        print(f"  [opensearch] ISM policy applied: {resp.status_code} (retention={settings.log_retention_days}d)")


def main() -> None:
    p = argparse.ArgumentParser(prog="app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("mfa-reset", help="Clear a user's MFA + recovery codes (break-glass).")
    r.add_argument("--email", required=True)

    b = sub.add_parser(
        "syslog-bootstrap",
        help="Ensure syslog CA, write receiver cert files, and apply OpenSearch index template + ISM policy.",
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
