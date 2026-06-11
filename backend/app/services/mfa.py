"""TOTP + recovery-code primitives for login MFA.

Pure functions over secrets/codes; persistence + encryption live in the API layer. The TOTP secret
is stored encrypted (MASTER_KEY) by the caller; recovery codes are argon2-hashed (one-time use)."""
import secrets
import time

import pyotp

from app.core.security import hash_password, verify_password

ISSUER = "OPNGMS"
_TOTP_PERIOD = 30


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, account: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=ISSUER)


def verify_totp(secret: str, code: str, *, last_used_step: int | None) -> tuple[bool, int | None]:
    """Verify a 6-digit code with ±1 step skew + anti-replay. Returns (ok, accepted_step)."""
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit():
        return False, None
    totp = pyotp.TOTP(secret)
    now = int(time.time())
    for offset in (0, -1, 1):
        step = now // _TOTP_PERIOD + offset
        if secrets.compare_digest(totp.at(step * _TOTP_PERIOD), code):
            if last_used_step is not None and step <= last_used_step:
                return False, None
            return True, step
    return False, None


def _code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # crockford-ish, no ambiguous chars
    raw = "".join(secrets.choice(alphabet) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


def generate_recovery_codes(n: int = 10) -> tuple[list[str], list[str]]:
    """Return (clear_codes, hashes). Store hashes; show clear codes to the user ONCE."""
    codes = [_code() for _ in range(n)]
    hashes = [hash_password(c) for c in codes]
    return codes, hashes


def find_recovery_match(code: str, hashes: list[str]) -> int | None:
    """Index of the hash matching `code`, or None. Caller marks that code used."""
    code = (code or "").strip().upper()
    for i, h in enumerate(hashes):
        if verify_password(code, h):
            return i
    return None
