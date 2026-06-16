import pytest

from app.services import webauthn as wa


def test_registration_options_includes_challenge_and_json():
    opts_json, challenge = wa.registration_options(
        user_id=b"\x01\x02", user_name="a@x.io", rp_id="opngms.test",
        rp_name="OPNGMS", existing_cred_ids=[])
    assert isinstance(opts_json, str) and "challenge" in opts_json
    assert isinstance(challenge, str) and challenge  # base64url, persisted on the session


def test_registration_options_excludes_existing_creds():
    opts_json, _ = wa.registration_options(
        user_id=b"\x01", user_name="a@x.io", rp_id="opngms.test",
        rp_name="OPNGMS", existing_cred_ids=[b"\xaa\xbb"])
    assert "excludeCredentials" in opts_json


def test_authentication_options_includes_challenge():
    opts_json, challenge = wa.authentication_options(rp_id="opngms.test", allow_cred_ids=[b"\xaa"])
    assert "challenge" in opts_json and challenge


def test_verify_authentication_accepts_increasing_sign_count(monkeypatch):
    class _V:
        new_sign_count = 6
    monkeypatch.setattr(wa, "_verify_auth_raw", lambda **k: _V())
    new = wa.verify_authentication(response={}, challenge="c", rp_id="r", origin="o",
                                   public_key=b"\x00", sign_count=5)
    assert new == 6


def test_verify_authentication_accepts_zero_counter(monkeypatch):
    # Authenticators with no counter report 0/0 — the documented "no counter" case is allowed.
    class _V:
        new_sign_count = 0
    monkeypatch.setattr(wa, "_verify_auth_raw", lambda **k: _V())
    new = wa.verify_authentication(response={}, challenge="c", rp_id="r", origin="o",
                                   public_key=b"\x00", sign_count=0)
    assert new == 0


def test_verify_authentication_rejects_non_increasing_sign_count(monkeypatch):
    class _V:  # what py_webauthn returns
        new_sign_count = 5
    monkeypatch.setattr(wa, "_verify_auth_raw", lambda **k: _V())
    # current stored sign_count 5 -> new 5 is NOT an increase -> reject
    with pytest.raises(wa.WebAuthnError):
        wa.verify_authentication(response={}, challenge="c", rp_id="r", origin="o",
                                 public_key=b"\x00", sign_count=5)


def test_verify_authentication_wraps_library_error(monkeypatch):
    def _boom(**k):
        raise ValueError("tampered")
    monkeypatch.setattr(wa, "_verify_auth_raw", _boom)
    with pytest.raises(wa.WebAuthnError):
        wa.verify_authentication(response={}, challenge="c", rp_id="r", origin="o",
                                 public_key=b"\x00", sign_count=0)


def test_verify_registration_wraps_library_error(monkeypatch):
    def _boom(**k):
        raise ValueError("bad attestation")
    monkeypatch.setattr(wa, "_verify_reg_raw", _boom)
    with pytest.raises(wa.WebAuthnError):
        wa.verify_registration(response={}, challenge="c", rp_id="r", origin="o")


def test_verify_registration_returns_library_result(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(wa, "_verify_reg_raw", lambda **k: sentinel)
    assert wa.verify_registration(response={}, challenge="c", rp_id="r", origin="o") is sentinel
