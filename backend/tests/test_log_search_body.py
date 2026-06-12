import uuid
from datetime import UTC, datetime

from app.services.log_search import MAX_SIZE, build_search_body


def _rng():
    return datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 6, 2, tzinfo=UTC)


def _body(**kw):
    base = dict(tenant_id=uuid.uuid4(), frm=_rng()[0], to=_rng()[1], query="", device_id=None,
                size=50, pit_id="PITID")
    base.update(kw)
    return build_search_body(**base)


def test_pit_and_tiebreaker_sort_always_present():
    tid = uuid.uuid4()
    body = _body(tenant_id=tid)
    assert body["pit"]["id"] == "PITID" and "keep_alive" in body["pit"]
    assert body["sort"] == [{"@timestamp": "desc"}, {"_shard_doc": "asc"}]
    flt = body["query"]["bool"]["filter"]
    assert {"term": {"tenant_id": str(tid)}} in flt
    assert any("range" in c and "@timestamp" in c["range"] for c in flt)
    assert body["track_total_hits"] is True
    assert "from" not in body
    assert "search_after" not in body
    assert "must" not in body["query"]["bool"]


def test_search_after_added_when_continuing():
    body = _body(search_after=["2026-06-01T00:00:00Z", 42])
    assert body["search_after"] == ["2026-06-01T00:00:00Z", 42]


def test_device_filter_and_guarded_query():
    did = uuid.uuid4()
    body = _body(query="action:block", device_id=did)
    assert {"term": {"device_id": str(did)}} in body["query"]["bool"]["filter"]
    qs = body["query"]["bool"]["must"][0]["query_string"]
    assert qs["query"] == "action:block" and qs["allow_leading_wildcard"] is False


def test_malicious_tenant_in_query_cannot_widen():
    tid = uuid.uuid4()
    body = _body(tenant_id=tid, query="tenant_id:other")
    assert {"term": {"tenant_id": str(tid)}} in body["query"]["bool"]["filter"]
    assert body["query"]["bool"]["must"][0]["query_string"]["query"] == "tenant_id:other"


def test_size_clamped_to_max():
    body = _body(size=9999)
    assert body["size"] == MAX_SIZE
