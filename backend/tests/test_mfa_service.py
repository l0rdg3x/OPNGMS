import pyotp

from app.services import mfa


def test_new_secret_and_uri():
    secret = mfa.new_secret()
    assert isinstance(secret, str) and len(secret) >= 16
    uri = mfa.provisioning_uri(secret, "user@x.io")
    assert uri.startswith("otpauth://totp/") and "OPNGMS" in uri


def test_verify_totp_accepts_current_and_rejects_replay():
    secret = mfa.new_secret()
    code = pyotp.TOTP(secret).now()
    ok, step = mfa.verify_totp(secret, code, last_used_step=None)
    assert ok and step is not None
    # same step replayed -> rejected
    ok2, _ = mfa.verify_totp(secret, code, last_used_step=step)
    assert not ok2


def test_verify_totp_rejects_wrong_code():
    secret = mfa.new_secret()
    ok, _ = mfa.verify_totp(secret, "000000", last_used_step=None)
    assert not ok


def test_recovery_codes_generate_hash_and_verify_once():
    codes, hashes = mfa.generate_recovery_codes(n=10)
    assert len(codes) == 10 and len(hashes) == 10
    # a clear code verifies against exactly its hash
    idx = mfa.find_recovery_match(codes[3], hashes)
    assert idx == 3
    assert mfa.find_recovery_match("not-a-code", hashes) is None
