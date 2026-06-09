import hashlib

import httpx
import respx

from app.connectors.opnsense.client import OpnsenseClient


@respx.mock
async def test_get_ids_alerts_normalizes():
    payload = {
        "rows": [
            {
                "timestamp": "2026-06-09T12:00:00+00:00",
                "src_ip": "10.0.0.5", "dest_ip": "1.2.3.4",
                "alert": {"signature": "ET SCAN Nmap", "severity": 2, "action": "allowed"},
                "alert_id": "abc123",
            }
        ]
    }
    respx.get(url__regex=r".*/api/ids/service/queryAlerts.*").mock(
        return_value=httpx.Response(200, json=payload)
    )
    client = OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)
    out = await client.get_ids_alerts(since=None)
    assert len(out) == 1
    e = out[0]
    assert e["src_ip"] == "10.0.0.5"
    assert e["dst_ip"] == "1.2.3.4"
    assert e["name"] == "ET SCAN Nmap"
    assert e["severity"] == "2"
    assert e["action"] == "allowed"
    assert e["category"] == "alert"
    assert e["event_key"] == "abc123"  # stable source id
    assert e["time"].tzinfo is not None  # datetime tz-aware


@respx.mock
async def test_get_ids_alerts_key_variants_and_hash_fallback():
    """Defensive toward key variants (alerts/dst_ip/signature at the top) and
    event_key derived from a discriminating hash when a stable id is missing."""
    payload = {
        "alerts": [
            {
                "timestamp": "2026-06-09T13:30:00Z",
                "src_ip": "10.0.0.7", "dst_ip": "8.8.8.8",
                "signature": "ET POLICY DNS", "severity": 3, "action": "blocked",
            }
        ]
    }
    respx.get(url__regex=r".*/api/ids/service/queryAlerts.*").mock(
        return_value=httpx.Response(200, json=payload)
    )
    client = OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)
    out = await client.get_ids_alerts(since=None)
    assert len(out) == 1
    e = out[0]
    assert e["src_ip"] == "10.0.0.7"
    assert e["dst_ip"] == "8.8.8.8"  # dst_ip key variant
    assert e["name"] == "ET POLICY DNS"  # signature at top-level
    assert e["severity"] == "3"
    assert e["action"] == "blocked"
    # no alert_id/_id -> discriminating hash of ts+src+dst+signature+severity
    expected = hashlib.sha1(
        "|".join([
            e["time"].isoformat(),
            "10.0.0.7", "8.8.8.8", "ET POLICY DNS", "3",
        ]).encode()
    ).hexdigest()
    assert e["event_key"] == expected
    assert e["time"].tzinfo is not None


@respx.mock
async def test_get_ids_alerts_event_key_is_discriminating():
    """Two distinct alerts (same signature but different src/dst) -> distinct event_keys.
    Guards against the risk flagged by the Task 1 review: do NOT collapse distinct events."""
    payload = {
        "rows": [
            {
                "timestamp": "2026-06-09T12:00:00+00:00",
                "src_ip": "10.0.0.5", "dest_ip": "1.2.3.4",
                "alert": {"signature": "ET SCAN Nmap", "severity": 2},
            },
            {
                "timestamp": "2026-06-09T12:00:00+00:00",
                "src_ip": "10.0.0.9", "dest_ip": "5.6.7.8",
                "alert": {"signature": "ET SCAN Nmap", "severity": 2},
            },
        ]
    }
    respx.get(url__regex=r".*/api/ids/service/queryAlerts.*").mock(
        return_value=httpx.Response(200, json=payload)
    )
    client = OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)
    out = await client.get_ids_alerts(since=None)
    assert len(out) == 2
    assert out[0]["event_key"] != out[1]["event_key"]


@respx.mock
async def test_get_ids_alerts_empty_payload():
    """Payload without rows/alerts -> empty list, no error."""
    respx.get(url__regex=r".*/api/ids/service/queryAlerts.*").mock(
        return_value=httpx.Response(200, json={})
    )
    client = OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)
    out = await client.get_ids_alerts(since=None)
    assert out == []
