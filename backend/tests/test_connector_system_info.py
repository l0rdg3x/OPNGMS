import httpx
import respx

from app.connectors.opnsense.client import OpnsenseClient

BASE = "https://203.0.113.10"
SYS_URL = f"{BASE}/api/diagnostics/system/systemInformation"


@respx.mock
async def test_get_system_info_parses_metrics():
    respx.get(SYS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "cpu": {"used": 12.5},
                "memory": {"used_pct": 41.0},
                "disk": {"used_pct": 23.0},
                "uptime_seconds": 86400,
            },
        )
    )
    info = await OpnsenseClient(BASE, "k", "s").get_system_info()
    assert info["cpu_pct"] == 12.5
    assert info["mem_pct"] == 41.0
    assert info["disk_pct"] == 23.0
    assert info["uptime_seconds"] == 86400
