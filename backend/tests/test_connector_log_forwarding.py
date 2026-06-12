import httpx
import respx

from app.connectors.opnsense.client import OpnsenseClient

BASE = "https://10.0.0.1"


def _client():
    return OpnsenseClient(BASE, "k", "s", verify_tls=False)


@respx.mock
async def test_import_ca_posts_existing_action():
    route = respx.post(url__regex=r".*/api/trust/ca/add.*").mock(
        return_value=httpx.Response(200, json={"result": "saved", "uuid": "ca-uuid"}))
    uuid_ = await _client().import_ca("-----CA PEM-----", descr="OPNGMS CA")
    assert uuid_ == "ca-uuid"
    body = route.calls[0].request.read().decode()
    assert "existing" in body


@respx.mock
async def test_import_cert_posts_import_with_key():
    route = respx.post(url__regex=r".*/api/trust/cert/add.*").mock(
        return_value=httpx.Response(200, json={"result": "saved", "uuid": "cert-uuid"}))
    uuid_ = await _client().import_cert("-----CERT-----", "-----KEY-----", descr="dev-9")
    assert uuid_ == "cert-uuid"
    body = route.calls[0].request.read().decode()
    assert "import" in body


@respx.mock
async def test_add_syslog_destination_then_reconfigure():
    add = respx.post(url__regex=r".*/api/syslog/settings/addDestination.*").mock(
        return_value=httpx.Response(200, json={"result": "saved", "uuid": "dest-uuid"}))
    rec = respx.post(url__regex=r".*/api/syslog/service/reconfigure.*").mock(
        return_value=httpx.Response(200, json={"status": "ok"}))
    uuid_ = await _client().add_syslog_destination(
        hostname="logs.example", port=6514, certificate_uuid="cert-uuid")
    assert uuid_ == "dest-uuid"
    assert add.called and rec.called
    body = add.calls[0].request.read().decode()
    assert "tls4" in body
    assert "cert-uuid" in body


@respx.mock
async def test_delete_syslog_destination_then_reconfigure():
    d = respx.post(url__regex=r".*/api/syslog/settings/delDestination/dest-uuid.*").mock(
        return_value=httpx.Response(200, json={"result": "deleted"}))
    rec = respx.post(url__regex=r".*/api/syslog/service/reconfigure.*").mock(
        return_value=httpx.Response(200, json={"status": "ok"}))
    await _client().delete_syslog_destination("dest-uuid")
    assert d.called and rec.called
