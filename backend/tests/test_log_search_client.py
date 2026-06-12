import uuid
from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.services.log_search import LogSearchError, search_logs


class _S:
    opensearch_url = "http://opensearch:9200"


_PIT = "http://opensearch:9200/opngms-logs-*/_pit"
_SEARCH = "http://opensearch:9200/_search"


def _args(**kw):
    base = dict(tenant_id=uuid.uuid4(), frm=datetime(2026, 6, 1, tzinfo=UTC),
                to=datetime(2026, 6, 2, tzinfo=UTC), query="", device_id=None, size=2)
    base.update(kw)
    return base


def _hit(i):
    return {"_id": str(i), "_source": {"@timestamp": f"2026-06-01T00:00:0{i}Z", "device_id": "d",
            "host": "fw", "program": "filterlog", "message": f"m{i}"}, "sort": [i, i]}


@respx.mock
async def test_first_page_opens_pit_and_returns_cursor():
    respx.post(_PIT).mock(return_value=httpx.Response(200, json={"pit_id": "PIT1"}))
    respx.post(_SEARCH).mock(return_value=httpx.Response(200, json={
        "pit_id": "PIT1", "hits": {"total": {"value": 9}, "hits": [_hit(1), _hit(2)]}}))
    res = await search_logs(_S(), **_args())
    assert res.total == 9 and len(res.hits) == 2
    assert res.hits[0].message == "m1"
    assert res.next_cursor == {"pit_id": "PIT1", "after": [2, 2]}


@respx.mock
async def test_partial_page_has_no_next_cursor():
    respx.post(_PIT).mock(return_value=httpx.Response(200, json={"pit_id": "PIT1"}))
    respx.post(_SEARCH).mock(return_value=httpx.Response(200, json={
        "pit_id": "PIT1", "hits": {"total": {"value": 1}, "hits": [_hit(1)]}}))
    res = await search_logs(_S(), **_args(size=2))
    assert res.next_cursor is None


@respx.mock
async def test_continuation_reuses_cursor_and_skips_pit():
    pit_route = respx.post(_PIT).mock(return_value=httpx.Response(200, json={"pit_id": "X"}))
    captured = {}

    def _search_responder(request):
        import json
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"pit_id": "PIT1", "hits": {"total": {"value": 9}, "hits": [_hit(3)]}})

    respx.post(_SEARCH).mock(side_effect=_search_responder)
    res = await search_logs(_S(), **_args(size=2, cursor={"pit_id": "PIT1", "after": [2, 2]}))
    assert not pit_route.called
    assert captured["pit"]["id"] == "PIT1"
    assert captured["search_after"] == [2, 2]
    assert res.next_cursor is None


@respx.mock
async def test_search_error_maps_to_logsearcherror():
    respx.post(_PIT).mock(return_value=httpx.Response(200, json={"pit_id": "PIT1"}))
    respx.post(_SEARCH).mock(return_value=httpx.Response(500, json={"error": "x"}))
    with pytest.raises(LogSearchError):
        await search_logs(_S(), **_args())
