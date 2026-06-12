from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from tools.opnsense_catalog.diff import diff_catalogs
from tools.opnsense_catalog.discover import discover_models
from tools.opnsense_catalog.emit import assemble_model, build_catalog, coverage_report
from tools.opnsense_catalog.endpoints import resolve_endpoints
from tools.opnsense_catalog.fetch import fetch_source
from tools.opnsense_catalog.form_parser import parse_forms
from tools.opnsense_catalog.model_parser import parse_model


def _generate(edition: str, version: str, source: Path) -> dict:
    models = []
    for src in discover_models(source):
        parsed = parse_model(Path(src.model_xml).read_text())
        if not parsed.mount:
            continue
        forms = parse_forms([(p.stem, p.read_text()) for p in src.form_paths])
        php = "\n".join(p.read_text() for p in src.controller_paths)
        eps, grid_eps, _conf = resolve_endpoints(src.module, parsed.grids, php or None)
        m = assemble_model(src.module, parsed, forms, eps, grid_eps, source="core")
        models.append(m)
    return build_catalog(models, edition=edition, version=version,
                         generated_from={"core": version})


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="opnsense-catalog")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--edition", default="community")
    g.add_argument("--version", required=True)
    g.add_argument("--source", help="path to an extracted source tree")
    g.add_argument("--fetch", action="store_true", help="download the tag first")
    g.add_argument("--out", required=True)
    d = sub.add_parser("diff")
    d.add_argument("a")
    d.add_argument("b")
    args = ap.parse_args(argv)

    if args.cmd == "generate":
        if args.fetch:
            tmp = Path(tempfile.mkdtemp())
            source = fetch_source("core", args.version, tmp)
        else:
            source = Path(args.source)
        cat = _generate(args.edition, args.version, source)
        Path(args.out).write_text(json.dumps(cat, indent=2, sort_keys=False) + "\n")
        rep = coverage_report(cat)
        print(json.dumps({"wrote": args.out, "coverage": rep}))
        return 0
    if args.cmd == "diff":
        a = json.loads(Path(args.a).read_text())
        b = json.loads(Path(args.b).read_text())
        print(json.dumps(diff_catalogs(a, b), indent=2))
        return 0
    return 1


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
