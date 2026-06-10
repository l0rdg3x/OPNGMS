"""Loader for the real-shape OPNsense response fixtures."""
import json
from pathlib import Path

_DIR = Path(__file__).parent / "fixtures" / "opnsense"


def load(name: str):
    """Return the parsed JSON of fixtures/opnsense/<name>."""
    return json.loads((_DIR / name).read_text(encoding="utf-8"))
