import uuid
from datetime import UTC, datetime

from app.services.log_search import MAX_SIZE, build_search_body


def _rng():
    return datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 6, 2, tzinfo=UTC)


def test_tenant_and_range_filters_always_present():
    tid = uuid.uuid4()
    frm, to = _rng()
    body = build_search_body(tenant_id=tid, frm=frm, to=to, query="", device_id=None, page=0, size=50)
    flt = body["query"]["bool"]["filter"]
    assert {"term": {"tenant_id": str(tid)}} in flt
    assert any("range" in c and "@timestamp" in c["range"] for c in flt)
    assert body["sort"] == [{"@timestamp": "desc"}]
    assert body["from"] == 0 and body["size"] == 50
    assert body["track_total_hits"] is True
    assert "must" not in body["query"]["bool"]  # no query -> no must clause


def test_device_filter_added():
    tid, did = uuid.uuid4(), uuid.uuid4()
    frm, to = _rng()
    body = build_search_body(tenant_id=tid, frm=frm, to=to, query="", device_id=did, page=0, size=10)
    assert {"term": {"device_id": str(did)}} in body["query"]["bool"]["filter"]


def test_query_string_is_guarded_and_in_must():
    tid = uuid.uuid4()
    frm, to = _rng()
    body = build_search_body(tenant_id=tid, frm=frm, to=to, query="action:block", device_id=None, page=2, size=25)
    must = body["query"]["bool"]["must"]
    qs = must[0]["query_string"]
    assert qs["query"] == "action:block"
    assert qs["allow_leading_wildcard"] is False
    assert qs["default_field"] == "message"
    assert body["from"] == 2 * 25


def test_malicious_tenant_in_query_cannot_widen():
    tid = uuid.uuid4()
    frm, to = _rng()
    body = build_search_body(tenant_id=tid, frm=frm, to=to, query="tenant_id:other", device_id=None, page=0, size=10)
    assert {"term": {"tenant_id": str(tid)}} in body["query"]["bool"]["filter"]
    assert body["query"]["bool"]["must"][0]["query_string"]["query"] == "tenant_id:other"


def test_size_clamped_to_max():
    tid = uuid.uuid4()
    frm, to = _rng()
    body = build_search_body(tenant_id=tid, frm=frm, to=to, query="", device_id=None, page=0, size=9999)
    assert body["size"] == MAX_SIZE
