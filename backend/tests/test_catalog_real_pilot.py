from pathlib import Path

from tools.opnsense_catalog.cli import _generate

# Real OPNsense 26.1.8 model/controller definitions, vendored under fixtures/.../real (no network).
_REAL = Path(__file__).parent / "fixtures/opnsense_catalog/real"


def test_three_real_models_parse_richly():
    cat = _generate("community", "26.1.8", _REAL)
    # ids/model_root come from the module dir (IDS/Unbound/Monit), NOT the mount — Unbound mounts at
    # //OPNsense/unboundplus but its API base is "unbound".
    assert {"ids", "unbound", "monit"} <= set(cat["models"])
    # Generality check: across the real modules the MAJORITY of fields are richly typed, not raw.
    total = sum(len(m["fields"]) for m in cat["models"].values())
    raw = sum(1 for m in cat["models"].values() for f in m["fields"] if f.get("confidence") == "raw")
    assert total > 20 and raw / total < 0.4
    assert cat["models"]["ids"]["endpoints"]["set"] == "ids/settings/set"
    # xml_path is the real config.xml mount (Unbound's differs from its API base)
    assert cat["models"]["unbound"]["xml_path"] == "OPNsense/unboundplus"
