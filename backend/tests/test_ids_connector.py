import httpx
import pytest
import respx

from app.connectors.opnsense.client import ApiError, OpnsenseClient


def _c():
    return OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)


@respx.mock
async def test_list_ids_rulesets_returns_rows():
    respx.get(url__regex=r".*/api/ids/settings/listRulesets.*").mock(
        return_value=httpx.Response(200, json={"total": 2, "rows": [
            {"filename": "a.rules", "description": "A", "enabled": "1", "documentation": "<a>x</a>"},
            {"filename": "b.rules", "description": "B", "enabled": "0", "documentation": "<a>y</a>"},
        ]}))
    rows = await _c().list_ids_rulesets()
    assert [r["filename"] for r in rows] == ["a.rules", "b.rules"]
    assert rows[1]["enabled"] == "0"


@respx.mock
async def test_apply_ids_rulesets_enables_each_then_reconfigures():
    toggled = []
    def _cap(request):
        toggled.append(str(request.url).split("/api/")[1])
        return httpx.Response(200, json={"status": "1"})
    respx.post(url__regex=r".*/api/ids/settings/toggleRuleset/.*").mock(side_effect=_cap)
    rec = respx.post(url__regex=r".*/api/ids/service/reconfigure.*").mock(
        return_value=httpx.Response(200, json={"status": "OK"}))
    res = await _c().apply_ids_rulesets(
        "set", {"rulesets": ["a.rules", "b.rules"]}, dry_run=False)
    assert toggled == ["ids/settings/toggleRuleset/a.rules/1",
                       "ids/settings/toggleRuleset/b.rules/1"]
    assert rec.called and res["dry_run"] is False and res["enabled"] == ["a.rules", "b.rules"]


@respx.mock
async def test_apply_ids_rulesets_dry_run_writes_nothing():
    t = respx.post(url__regex=r".*/api/ids/settings/toggleRuleset/.*")
    res = await _c().apply_ids_rulesets("set", {"rulesets": ["a.rules"]}, dry_run=True)
    assert not t.called and res["dry_run"] is True and res["rulesets"] == ["a.rules"]


async def test_apply_ids_rulesets_rejects_bad_filename():
    with pytest.raises(ApiError):
        await _c().apply_ids_rulesets(
            "set", {"rulesets": ["../../etc/passwd"]}, dry_run=False)
