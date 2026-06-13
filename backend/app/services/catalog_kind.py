"""Register the generic `catalog_setting` config-change applier (the version-aware editor's push).

The change payload carries the endpoints resolved at proposal time, so the applier is device-
independent: apply scalars (no per-call reconfigure), apply each grid op, then ONE reconfigure.
"""
from app.services.config_apply import register_change_applier

# Models the generic editor must never push — they can isolate the operator from the box.
# v1: interface assignment. The create endpoint refuses these (422); the read endpoint flags them.
CATALOG_DENYLIST = frozenset({"interfaces"})


async def _apply_catalog_setting(client, operation: str, payload: dict, *, dry_run: bool) -> dict:
    scalars = payload.get("scalars") or {}
    grids = payload.get("grids") or []
    result: dict = {"dry_run": dry_run, "scalars": None, "grids": []}
    if scalars:
        result["scalars"] = await client.apply_setting(
            payload["set_path"], payload["reconfigure_path"], payload["model_root"],
            scalars, dry_run=dry_run, reconfigure=False)
    for g in grids:
        result["grids"].append(await client.apply_grid_item(
            g["op"], g["endpoints"], row=g["row"], uuid=g.get("uuid"),
            item=g.get("item"), dry_run=dry_run))
    if not dry_run and (scalars or grids):
        await client.reconfigure(payload["reconfigure_path"])
    return result


register_change_applier("catalog_setting", _apply_catalog_setting)
