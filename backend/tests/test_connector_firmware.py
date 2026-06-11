import httpx
import pytest
import respx

from app.connectors.opnsense.client import ApiError, OpnsenseClient


def _client():
    return OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)


@respx.mock
async def test_firmware_check_and_status():
    chk = respx.post(url__regex=r".*/api/core/firmware/check.*").mock(
        return_value=httpx.Response(200, json={"status": "ok"}))
    stt = respx.get(url__regex=r".*/api/core/firmware/status.*").mock(
        return_value=httpx.Response(200, json={"status": "ok", "updates": "3", "download_size": "12M",
                                               "upgrade_needs_reboot": "1"}))
    assert (await _client().firmware_check())["status"] == "ok" and chk.called
    st = await _client().firmware_status_raw()
    assert st["updates"] == "3" and stt.called


@respx.mock
async def test_firmware_update_upgrade_and_status():
    up = respx.post(url__regex=r".*/api/core/firmware/update.*").mock(
        return_value=httpx.Response(200, json={"status": "ok", "msg_uuid": "x"}))
    ug = respx.post(url__regex=r".*/api/core/firmware/upgrade.*").mock(
        return_value=httpx.Response(200, json={"status": "ok"}))
    us = respx.get(url__regex=r".*/api/core/firmware/upgradestatus.*").mock(
        return_value=httpx.Response(200, json={"status": "running", "log": "..."}))
    assert (await _client().firmware_update())["status"] == "ok" and up.called
    assert (await _client().firmware_upgrade())["status"] == "ok" and ug.called
    assert (await _client().firmware_upgrade_status())["status"] == "running" and us.called


@respx.mock
async def test_plugin_install_remove_paths():
    ins = respx.post(url__regex=r".*/api/core/firmware/install/os-acme-client.*").mock(
        return_value=httpx.Response(200, json={"status": "ok"}))
    rem = respx.post(url__regex=r".*/api/core/firmware/remove/os-acme-client.*").mock(
        return_value=httpx.Response(200, json={"status": "ok"}))
    assert (await _client().plugin_install("os-acme-client"))["status"] == "ok" and ins.called
    assert (await _client().plugin_remove("os-acme-client"))["status"] == "ok" and rem.called


async def test_plugin_name_validation_rejects_injection():
    with pytest.raises(ApiError):
        await _client().plugin_install("../core/firmware/reboot")
    with pytest.raises(ApiError):
        await _client().plugin_remove("os bad name")
    with pytest.raises(ApiError):
        await _client().plugin_install("")
    with pytest.raises(ApiError):
        await _client().plugin_install("os-acme-client\n")
