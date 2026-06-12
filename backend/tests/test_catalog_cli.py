import json
import subprocess
import sys
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
    # fields with no form entry still group under a single "general" page (no duplicate page ids)
    page_ids = [p["id"] for p in ids["pages"]]
    assert page_ids == sorted(set(page_ids))


def test_diff_command(tmp_path, capsys):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps({"models": {"ids": {"fields": [{"path": "p", "type": "bool"}]}}}))
    b.write_text(json.dumps({"models": {"ids": {"fields": [{"path": "p", "type": "int"}]}}}))
    rc = main(["diff", str(a), str(b)])
    assert rc == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["models"]["ids"]["changed_fields"][0]["after"] == "int"


def test_module_runs_as_main(tmp_path):
    # The regen job + README invoke `python -m tools.opnsense_catalog.cli …`; the __main__ guard
    # must actually call main(). Run from the backend root so `-m tools.…` resolves.
    backend = Path(__file__).parents[1]
    out = tmp_path / "c.json"
    r = subprocess.run(
        [sys.executable, "-m", "tools.opnsense_catalog.cli", "generate",
         "--version", "26.1.8", "--source", str(_FIX), "--out", str(out)],
        cwd=backend, capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    assert out.exists() and json.loads(out.read_text())["version"] == "26.1.8"
