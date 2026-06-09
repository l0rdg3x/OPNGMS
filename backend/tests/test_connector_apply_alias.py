import httpx
import respx

from app.connectors.opnsense.client import OpnsenseClient


async def test_apply_alias_dry_run_does_no_http():
    # dry_run=True must perform NO HTTP and return a stub.
    client = OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)
    out = await client.apply_alias("set", {"name": "myalias", "content": ["1.2.3.4"]}, dry_run=True)
    assert out["dry_run"] is True
    assert out["operation"] == "set"


@respx.mock
async def test_apply_alias_real_posts_and_reconfigures():
    set_route = respx.post(url__regex=r".*/api/firewall/alias/setItem.*").mock(
        return_value=httpx.Response(200, json={"result": "saved"})
    )
    rec_route = respx.post(url__regex=r".*/api/firewall/alias/reconfigure.*").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )
    client = OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)
    out = await client.apply_alias("set", {"name": "myalias"}, dry_run=False)
    assert out["dry_run"] is False
    assert set_route.called and rec_route.called
