import uuid
from datetime import datetime, timezone

import pytest

from app.repositories.event import decode_cursor, encode_cursor


def test_cursor_roundtrip():
    t = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
    did = uuid.uuid4()
    c = encode_cursor(t, did, "suricata", "abc123")
    t2, did2, source2, ek2 = decode_cursor(c)
    assert t2 == t and did2 == did and source2 == "suricata" and ek2 == "abc123"


def test_decode_rejects_garbage():
    with pytest.raises(ValueError):
        decode_cursor("not-a-valid-cursor!!")
