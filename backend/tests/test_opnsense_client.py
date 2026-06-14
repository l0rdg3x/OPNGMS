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
