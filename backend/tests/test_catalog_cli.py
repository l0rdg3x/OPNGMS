import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from tools.opnsense_catalog.cli import main
from tools.opnsense_catalog.publish import sha256_hex

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


def test_generate_all_emits_catalogs_and_manifest(tmp_path):
    # Two "versions" both sourced from the same vendored minicore tree.
    root = tmp_path / "src"
    for v in ("26.1.7", "26.1.8"):
        shutil.copytree(_FIX, root / v)
    out = tmp_path / "out"
    rc = main(["generate-all", "--edition", "community",
               "--versions", "26.1.7,26.1.8",
               "--source-root", str(root), "--out-dir", str(out)])
    assert rc == 0
    cat = json.loads((out / "community-26.1.8.json").read_text())
    assert cat["version"] == "26.1.8"
    manifest = json.loads((out / "manifest.json").read_text())
    assert set(manifest["catalogs"]) == {"community/26.1.7", "community/26.1.8"}
    # The manifest sha must match the exact bytes written for that catalog.
    blob = (out / "community-26.1.8.json").read_bytes()
    assert manifest["catalogs"]["community/26.1.8"] == sha256_hex(blob)
    assert "generated_at" in manifest


_BIZ = Path(__file__).parent / "fixtures/opnsense_catalog/business"


def test_business_base_writes_map_from_html_dir(tmp_path):
    out = tmp_path / "business-base.json"
    rc = main(["business-base", "--html-dir", str(_BIZ), "--out", str(out)])
    assert rc == 0
    data = json.loads(out.read_text())
    assert data["map"] == {"26.4": "26.1.6", "25.10": "25.7.9"}
    assert "generated_at" in data


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


def test_generate_all_skips_versions_in_prior_manifest(tmp_path):
    root = tmp_path / "src"
    for v in ("26.1.7", "26.1.8"):
        shutil.copytree(_FIX, root / v)
    prior = tmp_path / "prior.json"
    prior.write_text(json.dumps({"catalogs": {"community/26.1.7": "PRIORSHA"}}))
    out = tmp_path / "out"
    rc = main(["generate-all", "--edition", "community", "--versions", "26.1.7,26.1.8",
               "--source-root", str(root), "--out-dir", str(out), "--prior-manifest", str(prior)])
    assert rc == 0
    assert not (out / "community-26.1.7.json").exists()   # already published -> skipped
    assert (out / "community-26.1.8.json").exists()        # new -> generated
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["catalogs"]["community/26.1.7"] == "PRIORSHA"   # carried verbatim
    assert "community/26.1.8" in manifest["catalogs"]               # freshly generated


def test_generate_all_force_regenerates_all(tmp_path):
    root = tmp_path / "src"
    for v in ("26.1.7", "26.1.8"):
        shutil.copytree(_FIX, root / v)
    prior = tmp_path / "prior.json"
    prior.write_text(json.dumps({"catalogs": {"community/26.1.7": "PRIORSHA"}}))
    out = tmp_path / "out"
    rc = main(["generate-all", "--edition", "community", "--versions", "26.1.7,26.1.8",
               "--source-root", str(root), "--out-dir", str(out),
               "--prior-manifest", str(prior), "--force"])
    assert rc == 0
    assert (out / "community-26.1.7.json").exists()   # regenerated despite prior
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["catalogs"]["community/26.1.7"] != "PRIORSHA"  # fresh sha replaces the carried one
