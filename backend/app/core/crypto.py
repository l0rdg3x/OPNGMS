from functools import lru_cache

from cryptography.fernet import Fernet, MultiFernet

from app.core.config import get_settings


@lru_cache(maxsize=4)
def _multifernet_cached(primary: str, old_keys: str) -> MultiFernet:
    keys = [Fernet(primary.encode())]
    keys += [Fernet(k.strip().encode()) for k in old_keys.split(",") if k.strip()]
    return MultiFernet(keys)  # encrypts with keys[0] (primary); decrypts with any


def _multifernet() -> MultiFernet:
    # Memoised on the key material so we don't rebuild/validate Fernet keys on every call
    # (matters when re-keying many rows). Settings is itself lru_cached; clearing its cache
    # (e.g. in tests) yields a fresh key tuple and therefore a fresh MultiFernet here.
    s = get_settings()
    return _multifernet_cached(s.master_key, s.master_key_old_keys)


def encrypt(plaintext: str) -> bytes:
    return _multifernet().encrypt(plaintext.encode())


def decrypt(ciphertext: bytes) -> str:
    return _multifernet().decrypt(bytes(ciphertext)).decode()


def encrypt_bytes(data: bytes) -> bytes:
    return _multifernet().encrypt(data)


def decrypt_bytes(token: bytes) -> bytes:
    return _multifernet().decrypt(bytes(token))


def rotate(token: bytes) -> bytes:
    """Re-encrypt an existing token under the primary key (decrypting with whichever key fits)."""
    return _multifernet().rotate(bytes(token))
