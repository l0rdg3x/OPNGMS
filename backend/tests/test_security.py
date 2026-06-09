from app.core.security import hash_password, verify_password


def test_hash_then_verify_roundtrip():
    h = hash_password("s3cret-pw")
    assert h != "s3cret-pw"  # not plaintext
    assert verify_password("s3cret-pw", h) is True
    assert verify_password("wrong", h) is False


def test_two_hashes_of_same_password_differ():
    assert hash_password("x") != hash_password("x")  # random salt
