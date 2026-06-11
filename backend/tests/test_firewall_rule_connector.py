import httpx
import pytest
import respx

from app.connectors.opnsense.client import ApiError, OpnsenseClient


def _c():
    return OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)


@respx.mock
async def test_get_firewall_rule_model_returns_rule():
    respx.get(url__regex=r".*/api/firewall/filter/getRule.*").mock(
        return_value=httpx.Response(200, json={"rule": {"action": {"pass": {"value": "Pass", "selected": 1}}}}))
    model = await _c().get_firewall_rule_model()
    assert "action" in model


@respx.mock
async def test_apply_firewall_rule_add_then_apply():
    # no existing rule with this (description, interface) -> addRule
    respx.post(url__regex=r".*/api/firewall/filter/searchRule.*").mock(
        return_value=httpx.Response(200, json={"rows": []}))
    posts = []
    def _cap(request):
        posts.append(str(request.url).split("/api/")[1])
        return httpx.Response(200, json={"result": "saved", "uuid": "u1"})
    respx.post(url__regex=r".*/api/firewall/filter/addRule.*").mock(side_effect=_cap)
    applied = respx.post(url__regex=r".*/api/firewall/filter/apply.*").mock(
        return_value=httpx.Response(200, json={"status": "OK"}))
    res = await _c().apply_firewall_rule(
        "set", {"description": "block-telnet", "interface": "wan", "action": "block"}, dry_run=False)
    assert any(p.startswith("firewall/filter/addRule") for p in posts)
    assert applied.called and res["operation"] == "add" and res["dry_run"] is False


@respx.mock
async def test_apply_firewall_rule_upsert_sets_existing():
    # exactly one existing rule with same description AND interface -> setRule/{uuid}
    respx.post(url__regex=r".*/api/firewall/filter/searchRule.*").mock(
        return_value=httpx.Response(200, json={"rows": [
            {"uuid": "u9", "description": "block-telnet", "interface": "wan"},
            {"uuid": "uX", "description": "block-telnet", "interface": "lan"},  # different iface, ignored
        ]}))
    setp = respx.post(url__regex=r".*/api/firewall/filter/setRule/u9.*").mock(
        return_value=httpx.Response(200, json={"result": "saved"}))
    respx.post(url__regex=r".*/api/firewall/filter/apply.*").mock(
        return_value=httpx.Response(200, json={"status": "OK"}))
    res = await _c().apply_firewall_rule(
        "set", {"description": "block-telnet", "interface": "wan"}, dry_run=False)
    assert setp.called and res["operation"] == "set"


@respx.mock
async def test_apply_firewall_rule_ambiguous_refuses():
    respx.post(url__regex=r".*/api/firewall/filter/searchRule.*").mock(
        return_value=httpx.Response(200, json={"rows": [
            {"uuid": "a", "description": "dup", "interface": "wan"},
            {"uuid": "b", "description": "dup", "interface": "wan"},
        ]}))
    add = respx.post(url__regex=r".*/api/firewall/filter/addRule.*")
    with pytest.raises(ApiError):
        await _c().apply_firewall_rule("set", {"description": "dup", "interface": "wan"}, dry_run=False)
    assert not add.called


@respx.mock
async def test_apply_firewall_rule_dry_run_writes_nothing():
    search = respx.post(url__regex=r".*/api/firewall/filter/searchRule.*")
    add = respx.post(url__regex=r".*/api/firewall/filter/addRule.*")
    res = await _c().apply_firewall_rule("set", {"description": "d", "interface": "wan"}, dry_run=True)
    assert not search.called and not add.called and res["dry_run"] is True
