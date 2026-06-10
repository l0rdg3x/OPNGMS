import uuid
import os

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import config as config_mod
from app.core import crypto
from app.scripts.rekey_secrets import rekey_all


@pytest.fixture
async def factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture(autouse=False)
def restore_env_rekey():
    """Snapshot and restore os.environ + settings cache around env-mutation tests."""
    original_master_key = os.environ.get("MASTER_KEY")
    original_old_keys = os.environ.get("MASTER_KEY_OLD_KEYS")
    config_mod.get_settings.cache_clear()
    yield
    if original_master_key is not None:
        os.environ["MASTER_KEY"] = original_master_key
    else:
        os.environ.pop("MASTER_KEY", None)
    if original_old_keys is not None:
        os.environ["MASTER_KEY_OLD_KEYS"] = original_old_keys
    else:
        os.environ.pop("MASTER_KEY_OLD_KEYS", None)
    config_mod.get_settings.cache_clear()


async def test_rekey_reencrypts_device_secrets(factory, restore_env_rekey):
    old = Fernet.generate_key().decode()
    new = Fernet.generate_key().decode()
    # Insert a device whose secrets are encrypted under the OLD key.
    config_mod.get_settings.cache_clear()
    os.environ["MASTER_KEY"] = old
    os.environ.pop("MASTER_KEY_OLD_KEYS", None)
    config_mod.get_settings.cache_clear()
    tid, did, sid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    enc_key, enc_sec = crypto.encrypt("api-key"), crypto.encrypt("api-secret")
    enc_content = crypto.encrypt_bytes(b"<config/>")  # config snapshot blob, under the OLD key
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'T','t','active')"), {"i": tid})
        await s.execute(
            text(
                "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags)"
                " VALUES (:i,:t,'d','https://d',:k,:s,true,'unverified','{}')"
            ),
            {"i": did, "t": tid, "k": enc_key, "s": enc_sec},
        )
        await s.execute(
            text(
                "INSERT INTO config_snapshots (id,tenant_id,device_id,canonical_hash,content_enc)"
                " VALUES (:i,:t,:d,'h1',:c)"
            ),
            {"i": sid, "t": tid, "d": did, "c": enc_content},
        )
        await s.commit()
    # Rotate: new primary, old kept for decryption.
    os.environ["MASTER_KEY"] = new
    os.environ["MASTER_KEY_OLD_KEYS"] = old
    config_mod.get_settings.cache_clear()
    n = await rekey_all(factory)
    assert n >= 2  # device (1) + config snapshot (1)
    # Now the secrets must decrypt with the NEW key alone.
    os.environ["MASTER_KEY"] = new
    os.environ.pop("MASTER_KEY_OLD_KEYS", None)
    config_mod.get_settings.cache_clear()
    async with factory() as s:
        row = (await s.execute(text("SELECT api_key_enc, api_secret_enc FROM devices WHERE id=:i"), {"i": did})).one()
        snap = (await s.execute(text("SELECT content_enc FROM config_snapshots WHERE id=:i"), {"i": sid})).one()
    assert crypto.decrypt(row.api_key_enc) == "api-key"
    assert crypto.decrypt(row.api_secret_enc) == "api-secret"
    assert crypto.decrypt_bytes(snap.content_enc) == b"<config/>"
