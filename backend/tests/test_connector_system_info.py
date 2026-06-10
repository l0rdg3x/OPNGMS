import httpx
import respx

from app.connectors.opnsense.client import OpnsenseClient
from tests.opn_fixtures import load

BASE = "https://203.0.113.10"


@respx.mock
async def test_get_system_info_aggregates_real_endpoints():
    respx.get(f"{BASE}/api/diagnostics/system/systemResources").mock(
        return_value=httpx.Response(200, json=load("system_resources.json")))
    respx.get(f"{BASE}/api/diagnostics/system/systemDisk").mock(
        return_value=httpx.Response(200, json=load("system_disk.json")))
    respx.get(f"{BASE}/api/diagnostics/system/systemTime").mock(
        return_value=httpx.Response(200, json=load("system_time.json")))
    respx.get(f"{BASE}/api/diagnostics/cpu_usage/getCPUType").mock(
        return_value=httpx.Response(200, json=load("cpu_type.json")))

    info = await OpnsenseClient(BASE, "k", "s").get_system_info()
    assert info["mem_pct"] == 8.9
    assert info["disk_pct"] == 1.0
    assert info["uptime_seconds"] == 674
    assert info["cpu_pct"] == 6.0
