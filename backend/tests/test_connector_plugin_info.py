import httpx
import respx

from app.connectors.opnsense.client import OpnsenseClient
from tests.opn_fixtures import load


@respx.mock
async def test_get_plugin_info_reads_plugin_array():
    respx.get(url__regex=r".*/api/core/firmware/info.*").mock(
        return_value=httpx.Response(200, json=load("firmware_info.json")))
    out = await OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False).get_plugin_info()
    assert out["product_version"] == "26.1.9"
    assert "os-wireguard" in out["plugins"]
    assert "base" not in out["plugins"]            # package array ignored
    assert "os-theme-cicada" not in out["plugins"]  # not installed
