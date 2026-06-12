import json
from pathlib import Path

from tools.opnsense_catalog.cli import main

_FIX = Path(__file__).parent / "fixtures/opnsense_catalog/minicore"


def test_generate_writes_catalog(tmp_path):
    out = tmp_path / "26.1.8.json"
    rc = main(["generate", "--edition", "community", "--version", "26.1.8",
               "--source", str(_FIX), "--out", str(out)])
    assert rc == 0
    cat = json.loads(out.read_text())
    assert cat["version"] == "26.1.8"
    ids = cat["models"]["ids"]
    assert ids["endpoints"]["set"] == "ids/settings/set"
    enabled = next(f for f in ids["fields"] if f["path"] == "general.enabled")
    assert enabled["type"] == "bool" and enabled["label"] == "Enabled"


def test_diff_command(tmp_path, capsys):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps({"models": {"ids": {"fields": [{"path": "p", "type": "bool"}]}}}))
    b.write_text(json.dumps({"models": {"ids": {"fields": [{"path": "p", "type": "int"}]}}}))
    rc = main(["diff", str(a), str(b)])
    assert rc == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["models"]["ids"]["changed_fields"][0]["after"] == "int"
