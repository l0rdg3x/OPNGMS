from cryptography.fernet import Fernet, MultiFernet

from app.core.config import get_settings


def _multifernet() -> MultiFernet:
    settings = get_settings()
    keys = [Fernet(settings.master_key.encode())]
    keys += [
        Fernet(k.strip().encode())
        for k in settings.master_key_old_keys.split(",")
        if k.strip()
    ]
    return MultiFernet(keys)  # encrypts with keys[0] (primary); decrypts with any


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
