import httpx
import pytest
import respx

from app.connectors.opnsense.client import ApiError, OpnsenseClient


def _c():
    return OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)


@respx.mock
async def test_get_monit_test_model_returns_test():
    respx.get(url__regex=r".*/api/monit/settings/getTest.*").mock(
        return_value=httpx.Response(200, json={"test": {"action": {"alert": {"value": "alert", "selected": 0}}}}))
    model = await _c().get_monit_test_model()
    assert "action" in model


@respx.mock
async def test_apply_monit_test_add_then_reconfigure():
    respx.post(url__regex=r".*/api/monit/settings/searchTest.*").mock(
        return_value=httpx.Response(200, json={"rows": []}))
    posts = []
    def _cap(request):
        posts.append(str(request.url).split("/api/")[1]); return httpx.Response(200, json={"result": "saved", "uuid": "u1"})
    respx.post(url__regex=r".*/api/monit/settings/addTest.*").mock(side_effect=_cap)
    rec = respx.post(url__regex=r".*/api/monit/service/reconfigure.*").mock(
        return_value=httpx.Response(200, json={"status": "ok"}))
    res = await _c().apply_monit_test(
        "set", {"name": "CPUHigh", "type": "SystemResource", "condition": "cpu usage is greater than 90%", "action": "alert"}, dry_run=False)
    assert any(p.startswith("monit/settings/addTest") for p in posts)
    assert rec.called and res["operation"] == "add" and res["dry_run"] is False


@respx.mock
async def test_apply_monit_test_upsert_sets_existing():
    respx.post(url__regex=r".*/api/monit/settings/searchTest.*").mock(
        return_value=httpx.Response(200, json={"rows": [{"uuid": "u9", "name": "CPUHigh"}, {"uuid": "uX", "name": "Other"}]}))
    setp = respx.post(url__regex=r".*/api/monit/settings/setTest/u9.*").mock(
        return_value=httpx.Response(200, json={"result": "saved"}))
    respx.post(url__regex=r".*/api/monit/service/reconfigure.*").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    res = await _c().apply_monit_test("set", {"name": "CPUHigh", "action": "alert"}, dry_run=False)
    assert setp.called and res["operation"] == "set"


@respx.mock
async def test_apply_monit_test_ambiguous_refuses():
    respx.post(url__regex=r".*/api/monit/settings/searchTest.*").mock(
        return_value=httpx.Response(200, json={"rows": [{"uuid": "a", "name": "dup"}, {"uuid": "b", "name": "dup"}]}))
    add = respx.post(url__regex=r".*/api/monit/settings/addTest.*")
    with pytest.raises(ApiError):
        await _c().apply_monit_test("set", {"name": "dup"}, dry_run=False)
    assert not add.called


@respx.mock
async def test_apply_monit_test_dry_run_writes_nothing():
    search = respx.post(url__regex=r".*/api/monit/settings/searchTest.*")
    add = respx.post(url__regex=r".*/api/monit/settings/addTest.*")
    res = await _c().apply_monit_test("set", {"name": "x"}, dry_run=True)
    assert not search.called and not add.called and res["dry_run"] is True
