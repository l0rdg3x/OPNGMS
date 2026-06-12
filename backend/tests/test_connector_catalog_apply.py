import json

import pytest
import respx
from httpx import Response

from app.connectors.opnsense.client import ApiError, OpnsenseClient


def _client():
    return OpnsenseClient("https://1.2.3.4", "k", "s", verify_tls=False)


_EPS = {
    "search": "unbound/settings/searchHostOverride",
    "add": "unbound/settings/addHostOverride",
    "set": "unbound/settings/setHostOverride",
    "del": "unbound/settings/delHostOverride",
}


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


@respx.mock
async def test_apply_grid_item_add():
    route = respx.post("https://1.2.3.4/api/unbound/settings/addHostOverride").mock(
        return_value=Response(200, json={"uuid": "new", "result": "saved"}))
    res = await _client().apply_grid_item(
        "add", _EPS, row="host", item={"hostname": "h"}, dry_run=False)
    assert route.called
    assert json.loads(route.calls[0].request.read()) == {"host": {"hostname": "h"}}
    assert res["dry_run"] is False


@respx.mock
async def test_apply_grid_item_set_embeds_uuid():
    route = respx.post("https://1.2.3.4/api/unbound/settings/setHostOverride/abc-123").mock(
        return_value=Response(200, json={"result": "saved"}))
    await _client().apply_grid_item(
        "set", _EPS, row="host", uuid="abc-123", item={"hostname": "h"}, dry_run=False)
    assert route.called


@respx.mock
async def test_apply_grid_item_del_embeds_uuid():
    route = respx.post("https://1.2.3.4/api/unbound/settings/delHostOverride/abc-123").mock(
        return_value=Response(200, json={"result": "deleted"}))
    await _client().apply_grid_item("del", _EPS, row="host", uuid="abc-123", dry_run=False)
    assert route.called


async def test_apply_grid_item_dry_run_no_post():
    res = await _client().apply_grid_item("add", _EPS, row="host", item={"x": "1"}, dry_run=True)
    assert res["dry_run"] is True


async def test_apply_grid_item_rejects_unsafe_uuid():
    with pytest.raises(ApiError):
        await _client().apply_grid_item(
            "del", _EPS, row="host", uuid="../../etc", dry_run=False)


async def test_apply_grid_item_rejects_unsafe_endpoint():
    bad = {**_EPS, "del": "unbound/settings/delHostOverride/../danger"}
    with pytest.raises(ApiError):
        await _client().apply_grid_item("del", bad, row="host", uuid="abc", dry_run=False)
