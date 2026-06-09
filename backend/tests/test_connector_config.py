import httpx
import respx

from app.connectors.opnsense.client import OpnsenseClient

XML = "<opnsense><system><hostname>fw1</hostname></system></opnsense>"


@respx.mock
async def test_get_config_backup_returns_raw_xml():
    respx.get(url__regex=r".*/api/core/backup/download/this.*").mock(
        return_value=httpx.Response(200, text=XML, headers={"content-type": "application/xml"})
    )
    client = OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)
    out = await client.get_config_backup()
    assert out == XML
    assert "<hostname>fw1</hostname>" in out
