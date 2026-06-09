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
    assert e["event_key"] == "abc123"  # id stabile della sorgente
    assert e["time"].tzinfo is not None  # datetime tz-aware


@respx.mock
async def test_get_ids_alerts_key_variants_and_hash_fallback():
    """Difensivo verso varianti di chiave (alerts/dst_ip/signature in cima) e
    event_key derivato da hash discriminante quando manca un id stabile."""
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
    assert e["dst_ip"] == "8.8.8.8"  # variante chiave dst_ip
    assert e["name"] == "ET POLICY DNS"  # signature al top-level
    assert e["severity"] == "3"
    assert e["action"] == "blocked"
    # nessun alert_id/_id -> hash discriminante di ts+src+dst+signature+severity
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
    """Due alert distinti (stessa signature ma src/dst diversi) -> event_key distinti.
    Presidia il rischio segnalato dal review Task 1: NON collassare eventi distinti."""
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
    """Payload senza rows/alerts -> lista vuota, nessun errore."""
    respx.get(url__regex=r".*/api/ids/service/queryAlerts.*").mock(
        return_value=httpx.Response(200, json={})
    )
    client = OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)
    out = await client.get_ids_alerts(since=None)
    assert out == []
