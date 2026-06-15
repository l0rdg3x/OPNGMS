import httpx
import respx

from app.connectors.opnsense.client import OpnsenseClient


def _c():
    return OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)


# --- firewall_rule delete ---


@respx.mock
async def test_firewall_rule_delete_resolves_uuid_then_deletes_and_applies():
    # exactly one rule matches (description, interface) -> delRule/{uuid} + apply
    respx.post(url__regex=r".*/api/firewall/filter/searchRule.*").mock(
        return_value=httpx.Response(200, json={"rows": [
            {"uuid": "u9", "description": "tpl-rule", "interface": "lan"},
            {"uuid": "uX", "description": "tpl-rule", "interface": "wan"},  # different iface, ignored
        ]}))
    deleted = respx.post(url__regex=r".*/api/firewall/filter/delRule/u9.*").mock(
        return_value=httpx.Response(200, json={"result": "deleted"}))
    applied = respx.post(url__regex=r".*/api/firewall/filter/apply.*").mock(
        return_value=httpx.Response(200, json={"status": "OK"}))
    res = await _c().apply_firewall_rule(
        "delete", {"description": "tpl-rule", "interface": "lan"}, dry_run=False)
    assert deleted.called and applied.called
    assert res["operation"] == "delete" and res["dry_run"] is False


@respx.mock
async def test_firewall_rule_delete_absent_is_clean_noop():
    respx.post(url__regex=r".*/api/firewall/filter/searchRule.*").mock(
        return_value=httpx.Response(200, json={"rows": []}))
    deleted = respx.post(url__regex=r".*/api/firewall/filter/delRule/.*")
    applied = respx.post(url__regex=r".*/api/firewall/filter/apply.*")
    res = await _c().apply_firewall_rule(
        "delete", {"description": "ghost", "interface": "lan"}, dry_run=False)
    assert not deleted.called and not applied.called
    assert res["result"] == "absent"


@respx.mock
async def test_firewall_rule_delete_dry_run_writes_nothing():
    search = respx.post(url__regex=r".*/api/firewall/filter/searchRule.*")
    deleted = respx.post(url__regex=r".*/api/firewall/filter/delRule/.*")
    res = await _c().apply_firewall_rule(
        "delete", {"description": "tpl-rule", "interface": "lan"}, dry_run=True)
    assert not search.called and not deleted.called
    assert res["dry_run"] is True and res["operation"] == "delete"


# --- monit_test delete ---


@respx.mock
async def test_monit_test_delete_resolves_uuid_then_deletes_and_reconfigures():
    respx.post(url__regex=r".*/api/monit/settings/searchTest.*").mock(
        return_value=httpx.Response(200, json={"rows": [
            {"uuid": "t9", "name": "tpl-test"},
            {"uuid": "tX", "name": "Other"},
        ]}))
    deleted = respx.post(url__regex=r".*/api/monit/settings/delTest/t9.*").mock(
        return_value=httpx.Response(200, json={"result": "deleted"}))
    rec = respx.post(url__regex=r".*/api/monit/service/reconfigure.*").mock(
        return_value=httpx.Response(200, json={"status": "ok"}))
    res = await _c().apply_monit_test("delete", {"name": "tpl-test"}, dry_run=False)
    assert deleted.called and rec.called
    assert res["operation"] == "delete" and res["dry_run"] is False


@respx.mock
async def test_monit_test_delete_absent_is_clean_noop():
    respx.post(url__regex=r".*/api/monit/settings/searchTest.*").mock(
        return_value=httpx.Response(200, json={"rows": []}))
    deleted = respx.post(url__regex=r".*/api/monit/settings/delTest/.*")
    rec = respx.post(url__regex=r".*/api/monit/service/reconfigure.*")
    res = await _c().apply_monit_test("delete", {"name": "ghost"}, dry_run=False)
    assert not deleted.called and not rec.called
    assert res["result"] == "absent"


@respx.mock
async def test_monit_test_delete_dry_run_writes_nothing():
    search = respx.post(url__regex=r".*/api/monit/settings/searchTest.*")
    deleted = respx.post(url__regex=r".*/api/monit/settings/delTest/.*")
    res = await _c().apply_monit_test("delete", {"name": "tpl-test"}, dry_run=True)
    assert not search.called and not deleted.called
    assert res["dry_run"] is True and res["operation"] == "delete"
