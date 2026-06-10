import httpx
import respx

from app.connectors.opnsense.client import OpnsenseClient
from tests.opn_fixtures import load

BASE = "https://203.0.113.10"


@respx.mock
async def test_get_interfaces_traffic_endpoint():
    respx.get(f"{BASE}/api/diagnostics/traffic/interface").mock(
        return_value=httpx.Response(200, json=load("traffic_interface.json")))
    ifs = await OpnsenseClient(BASE, "k", "s").get_interfaces()
    by = {i["name"]: i for i in ifs}
    assert by["WAN"] == {"name": "WAN", "up": True, "bytes_in": 394684.0, "bytes_out": 5116981.0}
    assert by["LAN"]["up"] is False


@respx.mock
async def test_get_gateways():
    respx.get(f"{BASE}/api/routes/gateway/status").mock(
        return_value=httpx.Response(200, json=load("gateway_status.json")))
    gws = await OpnsenseClient(BASE, "k", "s").get_gateways()
    by = {g["name"]: g for g in gws}
    assert by["WAN_DHCP"]["up"] is True and by["WAN_DHCP"]["rtt_ms"] == 0.0


@respx.mock
async def test_get_vpn_status_reads_rows():
    respx.get(f"{BASE}/api/wireguard/service/show").mock(
        return_value=httpx.Response(200, json=load("wireguard_show.json")))
    vpn = await OpnsenseClient(BASE, "k", "s").get_vpn_status()
    assert vpn == [{"name": "wg-site-a", "up": True}]
