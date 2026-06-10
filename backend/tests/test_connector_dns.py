import httpx
import respx

from app.connectors.opnsense.client import OpnsenseClient
from tests.opn_fixtures import load


def _client():
    return OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)


@respx.mock
async def test_get_dns_events_overview_endpoint():
    route = respx.get(url__regex=r".*/api/unbound/overview/searchQueries.*").mock(
        return_value=httpx.Response(200, json=load("unbound_search_queries.json")))
    out = await _client().get_dns_events(since=None)
    assert route.called
    e = out[0]
    assert e["src_ip"] == "192.168.1.50" and e["name"] == "example.com"
    assert e["action"] == "allowed" and e["category"] == "query"
    assert e["dst_ip"] == "" and e["severity"] == ""
    assert e["event_key"] and e["time"].tzinfo is not None


@respx.mock
async def test_get_dns_events_empty():
    respx.get(url__regex=r".*/api/unbound/overview/searchQueries.*").mock(
        return_value=httpx.Response(200, json=load("unbound_search_queries_empty.json")))
    assert await _client().get_dns_events() == []
