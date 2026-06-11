import httpx
import pytest
import respx

from app.connectors.opnsense.client import OpnsenseClient


def _c():
    return OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)


@respx.mock
async def test_get_setting():
    respx.get(url__regex=r".*/api/ids/settings/get.*").mock(
        return_value=httpx.Response(200, json={"ids": {"general": {"enabled": "0"}}}))
    out = await _c().get_setting("ids/settings/get")
    assert out["ids"]["general"]["enabled"] == "0"


@respx.mock
async def test_apply_setting_partial_then_reconfigure():
    captured = {}
    def _cap(request):
        import json
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"result": "saved"})
    respx.post(url__regex=r".*/api/ids/settings/set.*").mock(side_effect=_cap)
    rec = respx.post(url__regex=r".*/api/ids/service/reconfigure.*").mock(
        return_value=httpx.Response(200, json={"status": "OK"}))
    res = await _c().apply_setting(
        "ids/settings/set", "ids/service/reconfigure", "ids",
        {"general.enabled": "1", "general.homenet": "a,b"}, dry_run=False)
    # un-flattened under the model root; partial (only the templated fields)
    assert captured == {"ids": {"general": {"enabled": "1", "homenet": "a,b"}}}
    assert rec.called and res["dry_run"] is False


@respx.mock
async def test_apply_setting_dry_run_writes_nothing():
    s = respx.post(url__regex=r".*/api/ids/settings/set.*")
    res = await _c().apply_setting("ids/settings/set", "ids/service/reconfigure", "ids",
                                   {"general.enabled": "1"}, dry_run=True)
    assert not s.called and res["dry_run"] is True
