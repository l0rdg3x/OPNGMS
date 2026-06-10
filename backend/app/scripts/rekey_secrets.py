"""Re-encrypt all stored secrets under the primary MASTER_KEY (key rotation).

Run AFTER setting the new key as MASTER_KEY and moving the previous key into
MASTER_KEY_OLD_KEYS, as the DB owner (RLS-exempt):

    python -m app.scripts.rekey_secrets

Then, once it succeeds, the retired key can be removed from MASTER_KEY_OLD_KEYS.
"""
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import crypto
from app.core.config import get_settings


async def rekey_all(factory) -> int:
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
        await session.commit()
    return rotated


def _owner_url() -> str:
    s = get_settings()
    return s.admin_database_url or s.database_url


async def _main() -> None:
    engine = create_async_engine(_owner_url(), pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    n = await rekey_all(factory)
    await engine.dispose()
    print(f"re-keyed {n} encrypted records")


if __name__ == "__main__":
    asyncio.run(_main())
