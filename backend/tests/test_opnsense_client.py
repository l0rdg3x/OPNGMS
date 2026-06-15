import httpx
import pytest
import respx

from app.connectors.opnsense.client import (
    ApiError,
    AuthError,
    OpnsenseClient,
    ParseError,
    ReachabilityError,
)
from tests.opn_fixtures import load

BASE = "https://203.0.113.10"
FW_URL = f"{BASE}/api/core/firmware/status"


@respx.mock
async def test_success_returns_version_and_sends_basic_auth():
    route = respx.get(FW_URL).mock(
        return_value=httpx.Response(200, json={"product_version": "24.1.1"})
    )
    client = OpnsenseClient(BASE, "key", "sec")
    version = await client.test_connection()
    assert version == "24.1.1"
    assert route.called
    assert route.calls.last.request.headers["authorization"].startswith("Basic ")


@respx.mock
async def test_401_raises_auth_error():
    respx.get(FW_URL).mock(return_value=httpx.Response(401))
    with pytest.raises(AuthError):
        await OpnsenseClient(BASE, "key", "bad").test_connection()


@respx.mock
async def test_timeout_raises_reachability_error():
    respx.get(FW_URL).mock(side_effect=httpx.ConnectTimeout("timeout"))
    with pytest.raises(ReachabilityError):
        await OpnsenseClient(BASE, "key", "sec").test_connection()


@respx.mock
async def test_500_raises_api_error_with_status():
    respx.get(FW_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(ApiError) as ei:
        await OpnsenseClient(BASE, "key", "sec").test_connection()
    assert ei.value.status_code == 503


@respx.mock
async def test_non_json_raises_parse_error():
    respx.get(FW_URL).mock(return_value=httpx.Response(200, text="not json"))
    with pytest.raises(ParseError):
        await OpnsenseClient(BASE, "key", "sec").test_connection()


@respx.mock
async def test_test_connection_reads_nested_firmware_version():
    # Real core/firmware/status nests the version under product.product_version.
    respx.get(FW_URL).mock(return_value=httpx.Response(200, json=load("firmware_status.json")))
    version = await OpnsenseClient(BASE, "key", "sec").test_connection()
    assert version == "26.1.9"


def test_client_timeout_defaults_to_setting(monkeypatch):
    from app.connectors.opnsense.client import OpnsenseClient
    from app.core import config

    monkeypatch.setenv("OPNSENSE_HTTP_TIMEOUT", "3.5")
    config.get_settings.cache_clear()
    try:
        c = OpnsenseClient(BASE, "key", "sec")
        assert c._timeout == 3.5
        # an explicit timeout arg still overrides the setting
        c2 = OpnsenseClient(BASE, "key", "sec", timeout=1.0)
        assert c2._timeout == 1.0
    finally:
        config.get_settings.cache_clear()


@respx.mock
async def test_get_firewall_blocks_returns_blocks_only():
    url = f"{BASE}/api/diagnostics/firewall/log"
    respx.get(url).mock(return_value=httpx.Response(200, json=[
        {"action": "block", "src": "1.1.1.1", "dstport": "22", "interface": "igb0",
         "__timestamp__": "2026-06-14T10:00:00", "__digest__": "d1"},
        {"action": "pass", "src": "10.0.0.1", "__timestamp__": "2026-06-14T10:00:01", "__digest__": "d2"},
    ]))
    out = await OpnsenseClient(BASE, "k", "s").get_firewall_blocks()
    assert len(out) == 1 and out[0]["src_ip"] == "1.1.1.1" and out[0]["event_key"] == "d1"


@respx.mock
async def test_get_auth_failures_parses_audit_log_post():
    url = f"{BASE}/api/diagnostics/log/core/audit"
    route = respx.post(url).mock(return_value=httpx.Response(200, json={"rows": [
        {"timestamp": "2026-06-14T10:00:00", "process_name": "audit",
         "line": " authentication failed for user 'admin' from 203.0.113.7"},
    ]}))
    out = await OpnsenseClient(BASE, "k", "s").get_auth_failures()
    assert route.called  # the audit log is queried via POST, not GET
    assert len(out) == 1 and out[0]["src_ip"] == "203.0.113.7" and out[0]["name"] == "admin"


@respx.mock
async def test_get_service_events_parses_system_log_post():
    url = f"{BASE}/api/diagnostics/log/core/system"
    route = respx.post(url).mock(return_value=httpx.Response(200, json={"rows": [
        {"timestamp": "2026-06-14T10:00:00", "process_name": "shutdown",
         "severity": "notice", "line": "reboot by root"},
        {"timestamp": "2026-06-14T10:00:01", "process_name": "dhcp6c",
         "severity": "info", "line": "advertise contains NoAddrsAvail status"},
    ]}))
    out = await OpnsenseClient(BASE, "k", "s").get_service_events()
    assert route.called  # the system log is queried via POST, not GET
    assert len(out) == 1 and out[0]["category"] == "reboot" and out[0]["name"] == "reboot"
