import httpx
import respx

from app.connectors.opnsense.client import OpnsenseClient


@respx.mock
async def test_get_plugin_info_normalizes():
    payload = {
        "product_version": "24.7.2",
        "package": [
            {"name": "os-wireguard", "installed": "1"},
            {"name": "os-firewall", "installed": "1"},
            {"name": "os-not-installed", "installed": "0"},
        ],
    }
    respx.get(url__regex=r".*/api/core/firmware/info.*").mock(
        return_value=httpx.Response(200, json=payload)
    )
    client = OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)
    out = await client.get_plugin_info()
    assert out["product_version"] == "24.7.2"
    assert "os-wireguard" in out["plugins"]
    assert "os-firewall" in out["plugins"]
    assert "os-not-installed" not in out["plugins"]  # only installed
