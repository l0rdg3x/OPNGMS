from tools.opnsense_catalog.endpoints import resolve_endpoints
from tools.opnsense_catalog.types import Grid

_STD_PHP = "class GeneralController extends ApiMutableModelControllerBase { ... }"


def test_convention_endpoints_for_settings_and_grids():
    grids = [Grid(path="userDefinedRules.rule", fields=[])]
    eps, grid_eps, confidence = resolve_endpoints("IDS", grids, _STD_PHP)
    assert eps == {"get": "ids/settings/get", "set": "ids/settings/set",
                   "reconfigure": "ids/service/reconfigure"}
    assert grid_eps["userDefinedRules.rule"] == {
        "search": "ids/settings/searchRule", "add": "ids/settings/addRule",
        "set": "ids/settings/setRule", "del": "ids/settings/delRule"}
    assert confidence == "rich"


def test_non_mvc_controller_marks_raw():
    _, _, confidence = resolve_endpoints("Weird", [], "class X extends ApiControllerBase {}")
    assert confidence == "raw"
