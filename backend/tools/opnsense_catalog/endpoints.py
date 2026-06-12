from __future__ import annotations

from tools.opnsense_catalog.types import Grid


def resolve_endpoints(module: str, grids: list[Grid], controller_php: str | None
                      ) -> tuple[dict[str, str], dict[str, dict], str]:
    base = module.lower()
    endpoints = {
        "get": f"{base}/settings/get",
        "set": f"{base}/settings/set",
        "reconfigure": f"{base}/service/reconfigure",
    }
    grid_eps: dict[str, dict] = {}
    for g in grids:
        item = g.path.split(".")[-1]
        cap = item[:1].upper() + item[1:]
        grid_eps[g.path] = {
            "search": f"{base}/settings/search{cap}",
            "add": f"{base}/settings/add{cap}",
            "set": f"{base}/settings/set{cap}",
            "del": f"{base}/settings/del{cap}",
        }
    # MVC-standard controllers extend ApiMutableModelControllerBase; otherwise endpoints are unverified.
    confidence = "rich" if (controller_php and "ApiMutableModelControllerBase" in controller_php) else "raw"
    return endpoints, grid_eps, confidence
