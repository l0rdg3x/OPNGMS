import os
import uuid

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


def test_rekey_covers_all_encrypted_columns():
    """Guard: rekey_all MUST re-encrypt every Fernet `*_enc` LargeBinary column. Adding a new one
    without updating rekey_secrets.py (and this expected set) fails here — before it can silently
    become undecryptable after a real rotation."""
    from sqlalchemy import LargeBinary

    from app.models import Base

    found: dict[str, set[str]] = {}
    for table in Base.metadata.tables.values():
        for col in table.columns:
            if col.name.endswith("_enc") and isinstance(col.type, LargeBinary):
                found.setdefault(table.name, set()).add(col.name)
    expected = {
        "devices": {"api_key_enc", "api_secret_enc"},
        "config_snapshots": {"content_enc"},
        "user_mfa": {"totp_secret_enc"},
        "smtp_settings": {"password_enc"},
        "syslog_ca_key": {"key_enc"},
    }
    assert found == expected, (
        "Encrypted-column set changed — update rekey_secrets.rekey_all AND this expected set. "
        f"found={found} expected={expected}"
    )


async def test_rekey_reencrypts_mfa_smtp_syslog(factory, restore_env_rekey):
    """MFA TOTP, SMTP password, and syslog CA key must also re-key (they were previously missed)."""
    old = Fernet.generate_key().decode()
    new = Fernet.generate_key().decode()
    config_mod.get_settings.cache_clear()
    os.environ["MASTER_KEY"] = old
    os.environ.pop("MASTER_KEY_OLD_KEYS", None)
    config_mod.get_settings.cache_clear()
    uid = uuid.uuid4()
    enc_totp = crypto.encrypt("TOTPSECRET")
    enc_pw = crypto.encrypt("smtp-pw")
    enc_cakey = crypto.encrypt_bytes(b"-----BEGIN KEY-----")
    async with factory() as s:
        await s.execute(
            text("INSERT INTO users (id,email,name,password_hash,is_superadmin,status)"
                 " VALUES (:i,'u@x.io','U','h',false,'active')"), {"i": uid})
        await s.execute(
            text("INSERT INTO user_mfa (user_id,enabled,totp_secret_enc) VALUES (:i,true,:v)"),
            {"i": uid, "v": enc_totp})
        await s.execute(
            text("INSERT INTO smtp_settings (id,password_enc) VALUES (1,:v)"), {"v": enc_pw})
        # The key lives in the owner-only syslog_ca_key table (FK->syslog_ca.id); insert the cert first.
        await s.execute(text("INSERT INTO syslog_ca (id,cert_pem) VALUES (1,'PEM')"))
        await s.execute(
            text("INSERT INTO syslog_ca_key (id,key_enc) VALUES (1,:v)"), {"v": enc_cakey})
        await s.commit()
    os.environ["MASTER_KEY"] = new
    os.environ["MASTER_KEY_OLD_KEYS"] = old
    config_mod.get_settings.cache_clear()
    await rekey_all(factory)
    # Decrypt with the NEW key alone.
    os.environ["MASTER_KEY"] = new
    os.environ.pop("MASTER_KEY_OLD_KEYS", None)
    config_mod.get_settings.cache_clear()
    async with factory() as s:
        totp = (await s.execute(text("SELECT totp_secret_enc FROM user_mfa WHERE user_id=:i"), {"i": uid})).scalar_one()
        pw = (await s.execute(text("SELECT password_enc FROM smtp_settings WHERE id=1"))).scalar_one()
        cakey = (await s.execute(text("SELECT key_enc FROM syslog_ca_key WHERE id=1"))).scalar_one()
    assert crypto.decrypt(totp) == "TOTPSECRET"
    assert crypto.decrypt(pw) == "smtp-pw"
    assert crypto.decrypt_bytes(cakey) == b"-----BEGIN KEY-----"
