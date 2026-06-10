import importlib
import os

import pytest
from cryptography.fernet import Fernet

from app.core import config as config_mod
from app.core import crypto


def test_encrypt_decrypt_roundtrip():
    token = crypto.encrypt("api-secret-123")
    assert isinstance(token, bytes)
    assert token != b"api-secret-123"  # encrypted, not plaintext
    assert crypto.decrypt(token) == "api-secret-123"


def test_two_encryptions_differ_but_both_decrypt():
    a = crypto.encrypt("x")
    b = crypto.encrypt("x")
    assert a != b  # Fernet includes timestamp+IV
    assert crypto.decrypt(a) == crypto.decrypt(b) == "x"


# ---------------------------------------------------------------------------
# MultiFernet / old-keys tests — fixture ensures env is restored after each
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=False)
def restore_env():
    """Snapshot and restore os.environ + settings cache around env-mutation tests."""
    original_master_key = os.environ.get("MASTER_KEY")
    original_old_keys = os.environ.get("MASTER_KEY_OLD_KEYS")
    config_mod.get_settings.cache_clear()
    yield
    # Restore original state
    if original_master_key is not None:
        os.environ["MASTER_KEY"] = original_master_key
    else:
        os.environ.pop("MASTER_KEY", None)
    if original_old_keys is not None:
        os.environ["MASTER_KEY_OLD_KEYS"] = original_old_keys
    else:
        os.environ.pop("MASTER_KEY_OLD_KEYS", None)
    config_mod.get_settings.cache_clear()


def _reload_with(primary: str, old: str = ""):
    # Settings is lru_cached; rebuild it pointing at the given keys.
    config_mod.get_settings.cache_clear()
    os.environ["MASTER_KEY"] = primary
    if old:
        os.environ["MASTER_KEY_OLD_KEYS"] = old
    else:
        os.environ.pop("MASTER_KEY_OLD_KEYS", None)
    config_mod.get_settings.cache_clear()


def test_roundtrip_primary(restore_env):
    k = Fernet.generate_key().decode()
    _reload_with(k)
    token = crypto.encrypt("secret")
    assert crypto.decrypt(token) == "secret"


def test_decrypts_with_old_key_after_rotation(restore_env):
    old = Fernet.generate_key().decode()
    _reload_with(old)
    token = crypto.encrypt("secret")  # encrypted under the old key
    new = Fernet.generate_key().decode()
    _reload_with(new, old=old)        # new primary, old kept for decryption
    assert crypto.decrypt(token) == "secret"


def test_new_token_does_not_decrypt_with_only_old_key(restore_env):
    old = Fernet.generate_key().decode()
    new = Fernet.generate_key().decode()
    _reload_with(new, old=old)
    token = crypto.encrypt("s")       # under new primary
    _reload_with(old)                 # only the old key present
    from cryptography.fernet import InvalidToken
    with pytest.raises(InvalidToken):
        crypto.decrypt(token)
