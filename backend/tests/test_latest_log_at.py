import uuid

import httpx
import respx

from app.services.log_search import latest_log_at


class _S:
    opensearch_url = "http://opensearch:9200"


_URL = "http://opensearch:9200/opngms-logs-*/_search"


@respx.mock
async def test_latest_log_at_returns_timestamp():
    respx.post(_URL).mock(return_value=httpx.Response(200, json={
        "hits": {"hits": [{"_source": {"@timestamp": "2026-06-01T10:00:00Z"}}]}}))
    out = await latest_log_at(_S(), tenant_id=uuid.uuid4(), device_id=uuid.uuid4())
    assert out is not None
    assert out.year == 2026 and out.month == 6 and out.day == 1
    assert out.tzinfo is not None


@respx.mock
async def test_latest_log_at_none_on_empty():
    respx.post(_URL).mock(return_value=httpx.Response(200, json={"hits": {"hits": []}}))
    assert await latest_log_at(_S(), tenant_id=uuid.uuid4(), device_id=uuid.uuid4()) is None


@respx.mock
async def test_latest_log_at_none_on_error():
    respx.post(_URL).mock(return_value=httpx.Response(503, json={}))
    assert await latest_log_at(_S(), tenant_id=uuid.uuid4(), device_id=uuid.uuid4()) is None


@respx.mock
async def test_latest_log_at_none_on_malformed_hit():
    # A non-dict hit element must degrade to None, never raise into the request.
    respx.post(_URL).mock(return_value=httpx.Response(200, json={"hits": {"hits": [None, "x"]}}))
    assert await latest_log_at(_S(), tenant_id=uuid.uuid4(), device_id=uuid.uuid4()) is None
