import httpx
import respx

from app.connectors.opnsense.client import OpnsenseClient


def _client(**kw):
    return OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False, **kw)


@respx.mock
async def test_default_client_uses_current_endpoints():
    respx.get(url__regex=r".*/api/unbound/overview/searchQueries.*").mock(
        return_value=httpx.Response(200, json={"rows": []}))
    assert await _client().get_dns_events() == []   # no identity -> newest -> current endpoint


@respx.mock
async def test_old_series_client_uses_legacy_dns_endpoint():
    respx.get(url__regex=r".*/api/unbound/diagnostics/queries.*").mock(
        return_value=httpx.Response(200, json={"rows": [
            {"client": "10.0.0.7", "domain": "x.com", "action": "allowed"}]}))
    out = await _client(version="18.7.1").get_dns_events()
    assert out[0]["name"] == "x.com"


@respx.mock
async def test_set_identity_switches_profile():
    respx.get(url__regex=r".*/api/unbound/diagnostics/queries.*").mock(
        return_value=httpx.Response(200, json={"rows": []}))
    c = _client()
    c.set_identity("community", "19.1.0")
    assert await c.get_dns_events() == []   # now resolves to the legacy endpoint (mocked)
