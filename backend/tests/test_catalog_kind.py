from app.services.config_apply import apply_for_kind
from app.services.catalog_kind import CATALOG_DENYLIST  # noqa: F401  (constant exists)


class _FakeClient:
    def __init__(self):
        self.calls = []

    async def apply_setting(self, set_path, reconfigure_path, model_root, payload, *,
                            dry_run, reconfigure=True):
        self.calls.append(("setting", set_path, dict(payload), dry_run, reconfigure))
        return {"dry_run": dry_run, "result": "set"}

    async def apply_grid_item(self, op, endpoints, *, row, uuid=None, item=None, dry_run=True):
        self.calls.append(("grid", op, row, uuid, dry_run))
        return {"dry_run": dry_run, "op": op}

    async def reconfigure(self, path):
        self.calls.append(("reconfigure", path))
        return {"status": "ok"}


def _payload():
    return {
        "model_id": "unbound", "set_path": "unbound/settings/set",
        "reconfigure_path": "unbound/service/reconfigure", "model_root": "unbound",
        "scalars": {"general.enabled": "1"},
        "grids": [{"op": "add", "endpoints": {"add": "unbound/settings/addHostOverride"},
                   "row": "host", "item": {"hostname": "h"}}],
    }


async def test_catalog_setting_applies_scalars_grids_then_one_reconfigure():
    c = _FakeClient()
    await apply_for_kind(c, "catalog_setting", "set", _payload(), dry_run=False)
    kinds = [x[0] for x in c.calls]
    assert kinds == ["setting", "grid", "reconfigure"]
    # the scalar set must NOT self-reconfigure (batched at the end)
    assert c.calls[0][4] is False


async def test_catalog_setting_dry_run_no_reconfigure():
    c = _FakeClient()
    await apply_for_kind(c, "catalog_setting", "set", _payload(), dry_run=True)
    assert [x[0] for x in c.calls] == ["setting", "grid"]  # no reconfigure on dry-run


async def test_catalog_denylist_has_interfaces():
    assert "interfaces" in CATALOG_DENYLIST
