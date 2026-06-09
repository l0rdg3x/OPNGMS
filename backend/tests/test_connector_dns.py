import httpx
import respx

from app.connectors.opnsense.client import OpnsenseClient


@respx.mock
async def test_get_dns_events_normalizes():
    payload = {
        "rows": [
            {
                "timestamp": "2026-06-09T12:00:00+00:00",
                "client": "10.0.0.20",
                "domain": "example.com",
                "action": "allowed",
                "query_id": "q1",
            }
        ]
    }
    respx.get(url__regex=r".*/api/unbound/diagnostics/queries.*").mock(
        return_value=httpx.Response(200, json=payload)
    )
    client = OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)
    out = await client.get_dns_events(since=None)
    assert len(out) == 1
    e = out[0]
    assert e["src_ip"] == "10.0.0.20"
    assert e["name"] == "example.com"       # dominio = "sito visitato"
    assert e["action"] == "allowed"
    assert e["category"] == "query"
    assert e["dst_ip"] == ""
    assert e["severity"] == ""
    assert e["event_key"]                    # source id or hash
    assert e["time"].tzinfo is not None      # tz-aware


@respx.mock
async def test_get_dns_events_key_variants_and_empty():
    # key variants + hash fallback + empty payload
    payload = {
        "queries": [
            {"time": "2026-06-09T13:00:00Z", "client_ip": "10.0.0.21", "query": "blocked.test", "action": "blocked"}
        ]
    }
    respx.get(url__regex=r".*/api/unbound/diagnostics/queries.*").mock(
        return_value=httpx.Response(200, json=payload)
    )
    client = OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)
    out = await client.get_dns_events()
    assert out[0]["src_ip"] == "10.0.0.21"
    assert out[0]["name"] == "blocked.test"
    assert out[0]["action"] == "blocked"
    assert out[0]["event_key"]  # content hash (no id)

    respx.get(url__regex=r".*/api/unbound/diagnostics/queries.*").mock(
        return_value=httpx.Response(200, json={})
    )
    assert await client.get_dns_events() == []
