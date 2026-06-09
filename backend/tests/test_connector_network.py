import httpx
import respx

from app.connectors.opnsense.client import OpnsenseClient

BASE = "https://203.0.113.10"


@respx.mock
async def test_get_interfaces():
    respx.get(f"{BASE}/api/diagnostics/interface/getInterfaceStatistics").mock(
        return_value=httpx.Response(200, json={
            "interfaces": [
                {"name": "igb0", "status": "up", "bytes_received": 1000, "bytes_transmitted": 2000},
            ]
        })
    )
    ifs = await OpnsenseClient(BASE, "k", "s").get_interfaces()
    assert ifs == [{"name": "igb0", "up": True, "bytes_in": 1000.0, "bytes_out": 2000.0}]


@respx.mock
async def test_get_gateways():
    respx.get(f"{BASE}/api/routes/gateway/status").mock(
        return_value=httpx.Response(200, json={
            "items": [
                {"name": "WAN_GW", "status": "none", "delay": "12.3 ms", "loss": "0.0 %"},
                {"name": "WAN2_GW", "status": "down", "delay": "", "loss": "100.0 %"},
            ]
        })
    )
    gws = await OpnsenseClient(BASE, "k", "s").get_gateways()
    by = {g["name"]: g for g in gws}
    assert by["WAN_GW"]["up"] is True and by["WAN_GW"]["rtt_ms"] == 12.3
    assert by["WAN2_GW"]["up"] is False and by["WAN2_GW"]["loss_pct"] == 100.0


@respx.mock
async def test_get_vpn_status():
    respx.get(f"{BASE}/api/wireguard/service/show").mock(
        return_value=httpx.Response(200, json={"tunnels": [{"name": "wg0", "connected": True}]})
    )
    vpn = await OpnsenseClient(BASE, "k", "s").get_vpn_status()
    assert vpn == [{"name": "wg0", "up": True}]
