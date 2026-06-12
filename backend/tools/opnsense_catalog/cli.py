from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from tools.opnsense_catalog.diff import diff_catalogs
from tools.opnsense_catalog.discover import discover_models
from tools.opnsense_catalog.emit import assemble_model, build_catalog, coverage_report
from tools.opnsense_catalog.endpoints import resolve_endpoints
from tools.opnsense_catalog.fetch import fetch_source
from tools.opnsense_catalog.form_parser import parse_forms
from tools.opnsense_catalog.model_parser import parse_model
from tools.opnsense_catalog.publish import build_manifest, parse_business_base, release_versions


def _generate(edition: str, version: str, source: Path) -> dict:
    models = []
    for src in discover_models(source):
        parsed = parse_model(Path(src.model_xml).read_text())
        if not parsed.mount:
            print(f"SKIP {src.module}: model {src.model_xml} has no <mount>", file=sys.stderr)
            continue
        forms = parse_forms([(p.stem, p.read_text()) for p in src.form_paths])
        php = "\n".join(p.read_text() for p in src.controller_paths)
        eps, grid_eps, _conf = resolve_endpoints(src.module, parsed.grids, php or None)
        m = assemble_model(src.module, parsed, forms, eps, grid_eps, source="core")
        models.append(m)
    return build_catalog(models, edition=edition, version=version,
                         generated_from={"core": version})


def _write_catalog(edition: str, version: str, source: Path, out_dir: Path) -> tuple[str, bytes]:
    """Generate one catalog, write community-<version>.json, return (manifest-key, file bytes)."""
    cat = _generate(edition, version, source)
    blob = (json.dumps(cat, indent=2, sort_keys=False) + "\n").encode("utf-8")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{edition}-{version}.json").write_bytes(blob)
    return f"{edition}/{version}", blob


def _fetch_core_tags() -> list[str]:  # pragma: no cover — network, ops use only
    """All tags of opnsense/core via the GitHub API (paginated). Filtered by release_versions()."""
    import httpx

    tags: list[str] = []
    for page in range(1, 21):  # safety cap: 20 * 100 tags
        r = httpx.get("https://api.github.com/repos/opnsense/core/tags",
                      params={"per_page": 100, "page": page},
                      headers={"Accept": "application/vnd.github+json"},
                      timeout=30.0, follow_redirects=True)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        tags.extend(t["name"] for t in batch)
    return tags


def _fetch_business_pages() -> dict[str, str]:  # pragma: no cover — network, ops use only
    """Scrape the BE release index + each BE_<v>.html. Returns {business_version: html}."""
    import re

    import httpx

    index = httpx.get("https://docs.opnsense.org/releases.html", timeout=30.0,
                      follow_redirects=True).text
    versions = sorted(set(re.findall(r"BE_(\d+\.\d+)\.html", index)))
    pages: dict[str, str] = {}
    for v in versions:
        r = httpx.get(f"https://docs.opnsense.org/releases/BE_{v}.html",
                      timeout=30.0, follow_redirects=True)
        if r.status_code == 200:
            pages[v] = r.text
    return pages


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
    ga = sub.add_parser("generate-all")
    ga.add_argument("--edition", default="community")
    ga.add_argument("--versions", required=True, help="comma-separated, e.g. 26.1.7,26.1.8")
    ga.add_argument("--source-root", help="dir with one extracted source tree per version: <root>/<version>/")
    ga.add_argument("--fetch", action="store_true", help="download each tag instead of --source-root")
    ga.add_argument("--out-dir", required=True)
    bb = sub.add_parser("business-base")
    bb.add_argument("--html-dir", help="dir of vendored BE_<version>.html files")
    bb.add_argument("--fetch", action="store_true", help="scrape docs.opnsense.org instead")
    bb.add_argument("--out", required=True)
    lv = sub.add_parser("list-versions")
    lv.add_argument("--minimum", help="drop versions below this (e.g. 26.1)")
    lv.add_argument("--format", choices=["csv", "lines"], default="csv")
    args = ap.parse_args(argv)

    if args.cmd == "generate":
        with tempfile.TemporaryDirectory() as tmp:   # cleaned up even on the --source path (unused there)
            source = fetch_source("core", args.version, Path(tmp)) if args.fetch else Path(args.source)
            cat = _generate(args.edition, args.version, source)
        Path(args.out).write_text(json.dumps(cat, indent=2, sort_keys=False) + "\n")
        rep = coverage_report(cat)
        print(json.dumps({"wrote": args.out, "coverage": rep}))
        return 0
    if args.cmd == "generate-all":
        versions = [v.strip() for v in args.versions.split(",") if v.strip()]
        out_dir = Path(args.out_dir)
        entries: dict[str, bytes] = {}
        for version in versions:
            if args.fetch:
                with tempfile.TemporaryDirectory() as tmp:
                    source = fetch_source("core", version, Path(tmp))
                    key, blob = _write_catalog(args.edition, version, source, out_dir)
            else:
                source = Path(args.source_root) / version
                key, blob = _write_catalog(args.edition, version, source, out_dir)
            entries[key] = blob
        manifest = build_manifest(entries)
        manifest["generated_at"] = datetime.now(UTC).isoformat()
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps({"wrote": str(out_dir), "versions": versions}))
        return 0
    if args.cmd == "list-versions":
        versions = release_versions(_fetch_core_tags(), minimum=args.minimum)
        print(",".join(versions) if args.format == "csv" else "\n".join(versions))
        return 0
    if args.cmd == "business-base":
        if args.fetch:
            pages = _fetch_business_pages()
        else:
            pages = {}
            for p in sorted(Path(args.html_dir).glob("BE_*.html")):
                pages[p.stem.removeprefix("BE_")] = p.read_text()
        data = parse_business_base(pages)
        data["generated_at"] = datetime.now(UTC).isoformat()
        Path(args.out).write_text(json.dumps(data, indent=2) + "\n")
        print(json.dumps({"wrote": args.out, "count": len(data["map"])}))
        return 0
    if args.cmd == "diff":
        a = json.loads(Path(args.a).read_text())
        b = json.loads(Path(args.b).read_text())
        print(json.dumps(diff_catalogs(a, b), indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
