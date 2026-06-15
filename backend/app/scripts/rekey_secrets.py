"""Re-encrypt ALL stored secrets under the primary MASTER_KEY (key rotation).

Covers every Fernet-encrypted column: device API key/secret, config-snapshot blobs, MFA TOTP secrets,
the SMTP relay password, and the internal syslog CA private key. (A guard test enumerates the model's
`*_enc` columns and fails if a new one is added without being re-keyed here — see test_rekey.)

Run AFTER setting the new key as MASTER_KEY and moving the previous key into MASTER_KEY_OLD_KEYS, as the
DB owner (RLS-exempt):

    python -m app.scripts.rekey_secrets

Then, once it succeeds, the retired key can be removed from MASTER_KEY_OLD_KEYS.
"""
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import crypto
from app.core.config import get_settings


async def rekey_all(factory) -> int:
    """Re-encrypt EVERY Fernet-encrypted column under the primary key. Must cover ALL `*_enc` columns —
    a missed column would become undecryptable once the old key is retired (see test_rekey_covers_all)."""
    rotated = 0
    async with factory() as session:
        # devices: two bytea secret columns
        rows = (await session.execute(text("SELECT id, api_key_enc, api_secret_enc FROM devices"))).all()
        for r in rows:
            await session.execute(
                text("UPDATE devices SET api_key_enc=:k, api_secret_enc=:s WHERE id=:i"),
                {"i": r.id, "k": crypto.rotate(r.api_key_enc), "s": crypto.rotate(r.api_secret_enc)},
            )
            rotated += 1
        # config snapshots: one bytea blob column
        snaps = (await session.execute(text("SELECT id, content_enc FROM config_snapshots"))).all()
        for r in snaps:
            await session.execute(
                text("UPDATE config_snapshots SET content_enc=:c WHERE id=:i"),
                {"i": r.id, "c": crypto.rotate(r.content_enc)},
            )
            rotated += 1
        # MFA TOTP secrets
        mfa = (await session.execute(text("SELECT user_id, totp_secret_enc FROM user_mfa"))).all()
        for r in mfa:
            await session.execute(
                text("UPDATE user_mfa SET totp_secret_enc=:v WHERE user_id=:i"),
                {"i": r.user_id, "v": crypto.rotate(r.totp_secret_enc)},
            )
            rotated += 1
        # SMTP relay password (nullable — skip rows with no password set)
        smtp = (await session.execute(
            text("SELECT id, password_enc FROM smtp_settings WHERE password_enc IS NOT NULL")
        )).all()
        for r in smtp:
            await session.execute(
                text("UPDATE smtp_settings SET password_enc=:v WHERE id=:i"),
                {"i": r.id, "v": crypto.rotate(r.password_enc)},
            )
            rotated += 1
        # Internal syslog CA private key (owner-only table syslog_ca_key; rekey runs as owner)
        cas = (await session.execute(text("SELECT id, key_enc FROM syslog_ca_key"))).all()
        for r in cas:
            await session.execute(
                text("UPDATE syslog_ca_key SET key_enc=:v WHERE id=:i"),
                {"i": r.id, "v": crypto.rotate(r.key_enc)},
            )
            rotated += 1
        await session.commit()
    return rotated


def _owner_url() -> str:
    s = get_settings()
    # Require the owner URL explicitly: the app role (database_url) is NOBYPASSRLS, so without
    # app.current_tenant set the RLS predicate matches zero rows and the script would silently
    # re-key nothing ("re-keyed 0") — after which retiring the old key makes every secret
    # undecryptable. Fail loudly instead.
    if not s.admin_database_url:
        raise RuntimeError(
            "ADMIN_DATABASE_URL must be set (DB owner/superuser) for rekey_secrets to bypass RLS "
            "and see every tenant's rows. Refusing to run as the RLS-restricted app role."
        )
    return s.admin_database_url


async def _main() -> None:
    engine = create_async_engine(_owner_url(), pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    n = await rekey_all(factory)
    await engine.dispose()
    print(f"re-keyed {n} encrypted records")


if __name__ == "__main__":
    asyncio.run(_main())
