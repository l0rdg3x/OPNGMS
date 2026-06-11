import httpx
import pytest
import respx

from app.connectors.opnsense.client import ApiError, OpnsenseClient


def _client():
    return OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)


async def test_apply_alias_dry_run_does_no_http():
    out = await _client().apply_alias("set", {"name": "myalias"}, dry_run=True)
    assert out["dry_run"] is True and out["operation"] == "set"


@respx.mock
async def test_apply_alias_add():
    add = respx.post(url__regex=r".*/api/firewall/alias/addItem.*").mock(
        return_value=httpx.Response(200, json={"result": "saved", "uuid": "u1"}))
    rec = respx.post(url__regex=r".*/api/firewall/alias/reconfigure.*").mock(
        return_value=httpx.Response(200, json={"status": "ok"}))
    out = await _client().apply_alias(
        "add", {"name": "a", "type": "host", "content": "1.2.3.4"}, dry_run=False)
    assert out["dry_run"] is False and add.called and rec.called
    assert out["result"]["uuid"] == "u1"


@respx.mock
async def test_apply_alias_set_resolves_uuid_then_setitem():
    search = respx.post(url__regex=r".*/api/firewall/alias/searchItem.*").mock(
        return_value=httpx.Response(200, json={"rows": [{"uuid": "u9", "name": "myalias"}]}))
    setroute = respx.post(url__regex=r".*/api/firewall/alias/setItem/u9.*").mock(
        return_value=httpx.Response(200, json={"result": "saved"}))
    rec = respx.post(url__regex=r".*/api/firewall/alias/reconfigure.*").mock(
        return_value=httpx.Response(200, json={"status": "ok"}))
    out = await _client().apply_alias("set", {"name": "myalias", "content": "5.6.7.8"}, dry_run=False)
    assert search.called and setroute.called and rec.called and out["dry_run"] is False


@respx.mock
async def test_apply_alias_delete_resolves_uuid_then_delitem():
    respx.post(url__regex=r".*/api/firewall/alias/searchItem.*").mock(
        return_value=httpx.Response(200, json={"rows": [{"uuid": "u9", "name": "myalias"}]}))
    delroute = respx.post(url__regex=r".*/api/firewall/alias/delItem/u9.*").mock(
        return_value=httpx.Response(200, json={"result": "deleted"}))
    respx.post(url__regex=r".*/api/firewall/alias/reconfigure.*").mock(
        return_value=httpx.Response(200, json={"status": "ok"}))
    out = await _client().apply_alias("delete", {"name": "myalias"}, dry_run=False)
    assert delroute.called and out["dry_run"] is False


@respx.mock
async def test_apply_alias_set_no_exact_match_raises_and_no_mutation():
    # searchItem returns a substring match only (not exact) -> ApiError, no setItem call.
    respx.post(url__regex=r".*/api/firewall/alias/searchItem.*").mock(
        return_value=httpx.Response(200, json={"rows": [{"uuid": "u1", "name": "myalias_other"}]}))
    setroute = respx.post(url__regex=r".*/api/firewall/alias/setItem.*").mock(
        return_value=httpx.Response(200, json={"result": "saved"}))
    with pytest.raises(ApiError):
        await _client().apply_alias("set", {"name": "myalias"}, dry_run=False)
    assert not setroute.called


@respx.mock
async def test_apply_alias_set_multiple_exact_matches_raises():
    respx.post(url__regex=r".*/api/firewall/alias/searchItem.*").mock(
        return_value=httpx.Response(200, json={"rows": [
            {"uuid": "u1", "name": "myalias"}, {"uuid": "u2", "name": "myalias"}]}))
    with pytest.raises(ApiError):
        await _client().apply_alias("delete", {"name": "myalias"}, dry_run=False)


@respx.mock
async def test_apply_alias_joins_list_content():
    # OPNsense coerces a JSON list to the literal 'Array'; content must be a newline-joined string.
    captured = {}

    def _capture(request):
        import json
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"result": "saved", "uuid": "u1"})

    respx.post(url__regex=r".*/api/firewall/alias/addItem.*").mock(side_effect=_capture)
    respx.post(url__regex=r".*/api/firewall/alias/reconfigure.*").mock(
        return_value=httpx.Response(200, json={"status": "ok"}))
    await _client().apply_alias(
        "add", {"name": "a", "type": "host", "content": ["1.1.1.1", "2.2.2.2"]}, dry_run=False)
    assert captured["alias"]["content"] == "1.1.1.1\n2.2.2.2"  # joined, not a list / not "Array"


@respx.mock
async def test_apply_alias_string_content_unchanged():
    # A string content must be left untouched (no spurious join / re-encode).
    captured = {}

    def _capture(request):
        import json
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"result": "saved", "uuid": "u1"})

    respx.post(url__regex=r".*/api/firewall/alias/addItem.*").mock(side_effect=_capture)
    respx.post(url__regex=r".*/api/firewall/alias/reconfigure.*").mock(
        return_value=httpx.Response(200, json={"status": "ok"}))
    await _client().apply_alias(
        "add", {"name": "a", "type": "host", "content": "1.1.1.1"}, dry_run=False)
    assert captured["alias"]["content"] == "1.1.1.1"
