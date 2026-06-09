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
