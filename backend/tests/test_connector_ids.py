import hashlib

import httpx
import respx

from app.connectors.opnsense.client import OpnsenseClient
from tests.opn_fixtures import load


def _client():
    return OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)


@respx.mock
async def test_get_ids_alerts_uses_post_and_normalizes():
    route = respx.post(url__regex=r".*/api/ids/service/queryAlerts.*").mock(
        return_value=httpx.Response(200, json=load("ids_query_alerts.json")))
    out = await _client().get_ids_alerts(since=None)
    assert route.called                       # POST, not GET
    e = out[0]
    assert e["src_ip"] == "192.168.1.50" and e["dst_ip"] == "8.8.8.8"
    assert e["name"] == "ET SCAN Nmap" and e["severity"] == "2" and e["action"] == "allowed"
    assert e["event_key"] == "a1" and e["time"].tzinfo is not None


@respx.mock
async def test_get_ids_alerts_bare_list_does_not_crash():
    # Regression: the old GET returned a bare [] and crashed `.get()` with AttributeError.
    respx.post(url__regex=r".*/api/ids/service/queryAlerts.*").mock(
        return_value=httpx.Response(200, json=[]))
    assert await _client().get_ids_alerts(since=None) == []


@respx.mock
async def test_get_ids_alerts_hash_fallback_is_discriminating():
    payload = {"rows": [
        {"timestamp": "2026-06-09T12:00:00+00:00", "src_ip": "10.0.0.5", "dest_ip": "1.2.3.4",
         "alert": {"signature": "ET SCAN Nmap", "severity": 2}},
        {"timestamp": "2026-06-09T12:00:00+00:00", "src_ip": "10.0.0.9", "dest_ip": "5.6.7.8",
         "alert": {"signature": "ET SCAN Nmap", "severity": 2}}]}
    respx.post(url__regex=r".*/api/ids/service/queryAlerts.*").mock(
        return_value=httpx.Response(200, json=payload))
    out = await _client().get_ids_alerts(since=None)
    assert len(out) == 2 and out[0]["event_key"] != out[1]["event_key"]
