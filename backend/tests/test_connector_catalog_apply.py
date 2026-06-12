import respx
from httpx import Response

from app.connectors.opnsense.client import OpnsenseClient


def _client():
    return OpnsenseClient("https://1.2.3.4", "k", "s", verify_tls=False)


@respx.mock
async def test_apply_setting_can_suppress_reconfigure():
    setroute = respx.post("https://1.2.3.4/api/unbound/settings/set").mock(
        return_value=Response(200, json={"result": "saved"}))
    recroute = respx.post("https://1.2.3.4/api/unbound/service/reconfigure").mock(
        return_value=Response(200, json={"status": "ok"}))
    res = await _client().apply_setting(
        "unbound/settings/set", "unbound/service/reconfigure", "unbound",
        {"general.enabled": "1"}, dry_run=False, reconfigure=False)
    assert res["dry_run"] is False
    assert setroute.called and not recroute.called


@respx.mock
async def test_reconfigure_posts_the_path():
    route = respx.post("https://1.2.3.4/api/unbound/service/reconfigure").mock(
        return_value=Response(200, json={"status": "ok"}))
    await _client().reconfigure("unbound/service/reconfigure")
    assert route.called
