import uuid
from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.services.log_search import LogSearchError, search_logs


class _S:
    opensearch_url = "http://opensearch:9200"


@respx.mock
async def test_search_maps_hits():
    respx.post("http://opensearch:9200/opngms-logs-*/_search").mock(return_value=httpx.Response(200, json={
        "hits": {"total": {"value": 2}, "hits": [
            {"_id": "a", "_source": {"@timestamp": "2026-06-01T00:00:00Z", "tenant_id": "t", "device_id": "d",
                                     "host": "fw", "program": "filterlog", "message": "blocked"}},
        ]},
    }))
    res = await search_logs(_S(), tenant_id=uuid.uuid4(), frm=datetime(2026, 6, 1, tzinfo=UTC),
                            to=datetime(2026, 6, 2, tzinfo=UTC), query="", device_id=None, page=0, size=10)
    assert res.total == 2
    assert res.hits[0].id == "a"
    assert res.hits[0].program == "filterlog"
    assert res.hits[0].message == "blocked"
    assert res.hits[0].source["host"] == "fw"


@respx.mock
async def test_search_error_maps_to_logsearcherror():
    respx.post("http://opensearch:9200/opngms-logs-*/_search").mock(return_value=httpx.Response(500, json={"error": "x"}))
    with pytest.raises(LogSearchError):
        await search_logs(_S(), tenant_id=uuid.uuid4(), frm=datetime(2026, 6, 1, tzinfo=UTC),
                          to=datetime(2026, 6, 2, tzinfo=UTC), query="", device_id=None, page=0, size=10)
