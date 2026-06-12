from pathlib import Path

from tools.opnsense_catalog.discover import discover_models


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_pairs_model_with_module_forms_and_controllers(tmp_path):
    base = tmp_path / "core-26.1.8/src/opnsense/mvc/app"
    _write(base / "models/OPNsense/IDS/IDS.xml", "<model><mount>//OPNsense/IDS</mount></model>")
    _write(base / "views/OPNsense/IDS/forms/general.xml", "<form/>")
    _write(base / "controllers/OPNsense/IDS/Api/GeneralController.php", "class G {}")
    sources = discover_models(tmp_path)
    assert len(sources) == 1
    s = sources[0]
    assert s.module == "IDS"
    assert s.model_xml.endswith("IDS/IDS.xml")
    assert [p.name for p in s.form_paths] == ["general.xml"]
    assert [p.name for p in s.controller_paths] == ["GeneralController.php"]
