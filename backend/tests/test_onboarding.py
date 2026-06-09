import httpx
import respx

from app.services.onboarding import probe_device

BASE = "https://203.0.113.10"
FW_URL = f"{BASE}/api/core/firmware/status"


@respx.mock
async def test_probe_success_reachable_with_version():
    respx.get(FW_URL).mock(
        return_value=httpx.Response(200, json={"product_version": "24.7"})
    )
    result = await probe_device(BASE, "key", "sec", verify_tls=True)
    assert result.reachable is True
    assert result.firmware_version == "24.7"
    assert result.error is None


@respx.mock
async def test_probe_failure_unverified_with_error():
    respx.get(FW_URL).mock(return_value=httpx.Response(401))
    result = await probe_device(BASE, "key", "bad", verify_tls=True)
    assert result.reachable is False
    assert result.firmware_version is None
    assert "AuthError" in result.error
