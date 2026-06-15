import httpx
import pytest
import respx

from app.connectors.opnsense.client import OpnsenseClient


def _c():
    return OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False, timeout=5)


_LINE = (" user root@192.168.6.100 changed configuration to /conf/backup/config-1.xml in "
         "/api/firewall/filter/addRule /api/firewall/filter/addRule made changes")


@respx.mock
async def test_get_config_changes_posts_audit_and_parses():
    route = respx.post(url__regex=r".*/api/diagnostics/log/core/audit.*").mock(
        return_value=httpx.Response(200, json={"rows": [
            {"timestamp": "2026-06-15T19:25:38", "process_name": "audit", "severity": "Notice", "line": _LINE},
            {"timestamp": "2026-06-15T19:25:38", "process_name": "configd.py", "severity": "Informational",
             "line": " action allowed system.diag.log for user root"},
        ]}))
    out = await _c().get_config_changes()
    assert route.called
    assert len(out) == 1                        # the configd.py noise row is dropped
    assert out[0]["action"] == "api" and out[0]["category"] == "firewall"


@respx.mock
async def test_get_config_changes_empty_on_no_rows():
    respx.post(url__regex=r".*/api/diagnostics/log/core/audit.*").mock(
        return_value=httpx.Response(200, json={"rows": []}))
    assert await _c().get_config_changes() == []
