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


@respx.mock
async def test_apply_monit_test_attaches_to_system_service():
    # upsert (add) then attach to the system service
    respx.get(url__regex=r".*/api/monit/settings/searchTest.*").mock(return_value=httpx.Response(200, json={"rows": []}))
    respx.post(url__regex=r".*/api/monit/settings/searchTest.*").mock(return_value=httpx.Response(200, json={"rows": []}))
    add = respx.post(url__regex=r".*/api/monit/settings/addTest.*").mock(
        return_value=httpx.Response(200, json={"result": "saved", "uuid": "T1"}))
    respx.post(url__regex=r".*/api/monit/settings/searchService.*").mock(
        return_value=httpx.Response(200, json={"rows": [{"uuid": "SYS", "type": "system"}, {"uuid": "X", "type": "custom"}]}))
    respx.get(url__regex=r".*/api/monit/settings/getService/SYS.*").mock(
        return_value=httpx.Response(200, json={"service": {"tests": {"OLD": {"value": "Old", "selected": 1}, "T1": {"value": "New", "selected": 0}}}}))
    captured = {}
    def _set(request):
        import json as _j; captured.update(_j.loads(request.content)); return httpx.Response(200, json={"result": "saved"})
    respx.post(url__regex=r".*/api/monit/settings/setService/SYS.*").mock(side_effect=_set)
    respx.post(url__regex=r".*/api/monit/service/reconfigure.*").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    res = await _c().apply_monit_test("set", {"name": "CPUHigh", "type": "SystemResource", "action": "alert", "attach_to_system": "1"}, dry_run=False)
    assert add.called and res["attached"] is True
    # the attach merged the new uuid into the service's existing tests, and the sent test payload had NO attach flag
    assert set(captured["service"]["tests"].split(",")) == {"OLD", "T1"}
    import json as _j
    sent_test = _j.loads(add.calls[0].request.content)["test"]
    assert "attach_to_system" not in sent_test


@respx.mock
async def test_apply_monit_test_no_attach_when_flag_off():
    respx.post(url__regex=r".*/api/monit/settings/searchTest.*").mock(return_value=httpx.Response(200, json={"rows": []}))
    respx.post(url__regex=r".*/api/monit/settings/addTest.*").mock(return_value=httpx.Response(200, json={"result": "saved", "uuid": "T1"}))
    svc = respx.post(url__regex=r".*/api/monit/settings/searchService.*")
    respx.post(url__regex=r".*/api/monit/service/reconfigure.*").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    res = await _c().apply_monit_test("set", {"name": "CPUHigh", "type": "SystemResource", "action": "alert"}, dry_run=False)
    assert not svc.called and res["attached"] is False


@respx.mock
async def test_attach_refuses_ambiguous_system_service():
    respx.post(url__regex=r".*/api/monit/settings/searchTest.*").mock(return_value=httpx.Response(200, json={"rows": []}))
    respx.post(url__regex=r".*/api/monit/settings/addTest.*").mock(return_value=httpx.Response(200, json={"result": "saved", "uuid": "T1"}))
    respx.post(url__regex=r".*/api/monit/settings/searchService.*").mock(
        return_value=httpx.Response(200, json={"rows": [{"uuid": "A", "type": "system"}, {"uuid": "B", "type": "system"}]}))
    with pytest.raises(ApiError):
        await _c().apply_monit_test("set", {"name": "X", "type": "SystemResource", "action": "alert", "attach_to_system": "1"}, dry_run=False)
