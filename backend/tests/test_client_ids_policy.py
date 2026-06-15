import json

import httpx
import pytest
import respx

from app.connectors.opnsense.client import ApiError, OpnsenseClient

_BODY = {
    "description": "Drop ET", "enabled": "1", "prio": "0",
    "action": ["alert", "drop"], "rulesets": ["et.rules"],
    "content": {"severity": ["1"]}, "new_action": "drop",
}


def _c():
    return OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)


def _capture(posted):
    """A respx side-effect that records (endpoint-path, parsed-json-body) for every POST."""
    def _side_effect(request):
        path = str(request.url).split("/api/")[1]
        try:
            body = json.loads(request.content) if request.content else {}
        except ValueError:
            body = {}
        posted.append((path, body))
        return httpx.Response(200, json={"result": "saved", "uuid": "newp"})
    return _side_effect


def _mock_get_policy(rulesets_options):
    """Mock GET ids/settings/getPolicy returning the enabled-ruleset relation option map."""
    respx.get(url__regex=r".*/api/ids/settings/getPolicy.*").mock(
        return_value=httpx.Response(200, json={"policy": {"rulesets": rulesets_options}}))


def _mock_search(rows):
    respx.post(url__regex=r".*/api/ids/settings/searchPolicy.*").mock(
        return_value=httpx.Response(200, json={"rows": rows}))


@respx.mock
async def test_dry_run_no_mutation():
    add = respx.post(url__regex=r".*/api/ids/settings/addPolicy.*")
    search = respx.post(url__regex=r".*/api/ids/settings/searchPolicy.*")
    res = await _c().apply_ids_policy("set", _BODY, dry_run=True)
    assert res["dry_run"] is True
    assert not add.called and not search.called


@respx.mock
async def test_set_adds_when_absent():
    posted = []
    _mock_search([])                                          # no existing policy -> addPolicy
    _mock_get_policy({"uuid-et": {"value": "et.rules", "selected": 1}})
    respx.post(url__regex=r".*/api/ids/settings/addPolicy.*").mock(side_effect=_capture(posted))
    respx.post(url__regex=r".*/api/ids/service/reconfigure.*").mock(side_effect=_capture(posted))
    res = await _c().apply_ids_policy("set", _BODY, dry_run=False)
    paths = [p for p, _ in posted]
    assert "ids/settings/addPolicy" in paths
    assert "ids/service/reconfigure" in paths
    body = next(b for p, b in posted if p == "ids/settings/addPolicy")
    policy = body["policy"]
    assert policy["action"] == "alert,drop"                  # comma-joined
    assert policy["rulesets"] == "uuid-et"                   # filename resolved to uuid
    assert policy["new_action"] == "drop"
    assert policy["content"] == "severity.1"   # {key:[values]} -> comma-joined "key.value" OptionField tokens
    assert res["operation"] == "add" and res["dry_run"] is False


@respx.mock
async def test_failed_validation_raises():
    # OPNsense returns HTTP 200 {"result":"failed",...} on a REJECTED policy — the connector must raise,
    # not silently report success (live-verified: a bad content filter returned failed but was reported OK).
    _mock_search([])
    _mock_get_policy({"uuid-et": {"value": "et.rules", "selected": 1}})
    respx.post(url__regex=r".*/api/ids/settings/addPolicy.*").mock(return_value=httpx.Response(
        200, json={"result": "failed", "validations": {"policy.content": "Policy rule not found."}}))
    reconf = respx.post(url__regex=r".*/api/ids/service/reconfigure.*")
    with pytest.raises(ApiError):
        await _c().apply_ids_policy("set", _BODY, dry_run=False)
    assert not reconf.called   # never reconfigure after a rejected apply


@respx.mock
async def test_set_updates_when_present():
    posted = []
    _mock_get_policy({"uuid-et": {"value": "et.rules", "selected": 1}})
    respx.post(url__regex=r".*/api/ids/settings/searchPolicy.*").mock(
        return_value=httpx.Response(200, json={"rows": [{"uuid": "p1", "description": "Drop ET"}]}))
    respx.post(url__regex=r".*/api/ids/settings/setPolicy/p1.*").mock(side_effect=_capture(posted))
    respx.post(url__regex=r".*/api/ids/service/reconfigure.*").mock(side_effect=_capture(posted))
    res = await _c().apply_ids_policy("set", _BODY, dry_run=False)
    assert any(p == "ids/settings/setPolicy/p1" for p, _ in posted)
    policy = next(b for p, b in posted if p == "ids/settings/setPolicy/p1")["policy"]
    assert policy["action"] == "alert,drop"                  # body shape verified on the update path too
    assert policy["rulesets"] == "uuid-et"
    assert policy["content"] == "severity.1"   # {key:[values]} -> comma-joined "key.value" OptionField tokens
    assert res["operation"] == "set"


@respx.mock
async def test_set_with_no_rulesets():
    # A policy with no ruleset filter takes the empty-list fast-path: getPolicy is NOT consulted.
    posted = []
    _mock_search([])
    get_policy = respx.get(url__regex=r".*/api/ids/settings/getPolicy.*")
    respx.post(url__regex=r".*/api/ids/settings/addPolicy.*").mock(side_effect=_capture(posted))
    respx.post(url__regex=r".*/api/ids/service/reconfigure.*").mock(side_effect=_capture(posted))
    res = await _c().apply_ids_policy("set", {**_BODY, "rulesets": []}, dry_run=False)
    policy = next(b for p, b in posted if p == "ids/settings/addPolicy")["policy"]
    assert policy["rulesets"] == ""                          # empty -> no relation resolution
    assert not get_policy.called                             # fast-path skipped the model read
    assert res["operation"] == "add"


@respx.mock
async def test_delete():
    posted = []
    respx.post(url__regex=r".*/api/ids/settings/searchPolicy.*").mock(
        return_value=httpx.Response(200, json={"rows": [{"uuid": "p1", "description": "Drop ET"}]}))
    respx.post(url__regex=r".*/api/ids/settings/delPolicy/p1.*").mock(side_effect=_capture(posted))
    respx.post(url__regex=r".*/api/ids/service/reconfigure.*").mock(side_effect=_capture(posted))
    res = await _c().apply_ids_policy("delete", _BODY, dry_run=False)
    assert any(p == "ids/settings/delPolicy/p1" for p, _ in posted)
    assert res["operation"] == "delete"


@respx.mock
async def test_delete_absent_is_noop():
    _mock_search([])
    deleted = respx.post(url__regex=r".*/api/ids/settings/delPolicy/.*")
    res = await _c().apply_ids_policy("delete", _BODY, dry_run=False)
    assert not deleted.called and res["result"] == "absent"


@respx.mock
async def test_unknown_ruleset_raises():
    _mock_search([])
    _mock_get_policy({})                                      # et.rules not enabled -> not in the map
    add = respx.post(url__regex=r".*/api/ids/settings/addPolicy.*")
    with pytest.raises(ApiError):
        await _c().apply_ids_policy("set", _BODY, dry_run=False)
    assert not add.called


@respx.mock
async def test_ambiguous_description_raises():
    _mock_get_policy({"uuid-et": {"value": "et.rules", "selected": 1}})
    respx.post(url__regex=r".*/api/ids/settings/searchPolicy.*").mock(
        return_value=httpx.Response(200, json={"rows": [
            {"uuid": "a", "description": "Drop ET"},
            {"uuid": "b", "description": "Drop ET"},
        ]}))
    add = respx.post(url__regex=r".*/api/ids/settings/addPolicy.*")
    with pytest.raises(ApiError):
        await _c().apply_ids_policy("set", _BODY, dry_run=False)
    assert not add.called
