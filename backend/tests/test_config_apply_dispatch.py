import pytest

from app.services import config_apply as ca


async def test_alias_applier_is_registered_and_dispatches():
    calls = {}

    class FakeClient:
        async def apply_alias(self, operation, payload, *, dry_run):
            calls["args"] = (operation, payload, dry_run)
            return {"dry_run": dry_run, "result": "ok"}

    res = await ca.apply_for_kind(FakeClient(), "alias", "set", {"name": "a"}, dry_run=True)
    assert calls["args"] == ("set", {"name": "a"}, True)
    assert res["result"] == "ok"


async def test_unknown_kind_raises():
    with pytest.raises(ca.UnknownChangeKindError):
        await ca.apply_for_kind(object(), "nope", "set", {}, dry_run=True)


async def test_register_a_custom_applier():
    async def _applier(client, operation, payload, *, dry_run):
        return {"applied": operation, "dry_run": dry_run}

    ca.register_change_applier("custom_demo", _applier)
    try:
        res = await ca.apply_for_kind(object(), "custom_demo", "set", {}, dry_run=False)
        assert res == {"applied": "set", "dry_run": False}
    finally:
        ca.CHANGE_APPLIERS.pop("custom_demo", None)
