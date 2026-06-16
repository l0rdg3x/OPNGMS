from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from tools.opnsense_catalog.diff import diff_catalogs
from tools.opnsense_catalog.discover import discover_models, discover_plugin_models
from tools.opnsense_catalog.emit import assemble_model, build_catalog, coverage_report
from tools.opnsense_catalog.endpoints import resolve_endpoints
from tools.opnsense_catalog.fetch import fetch_source
from tools.opnsense_catalog.form_parser import parse_forms
from tools.opnsense_catalog.menu import discover_menus, merge_menus, parse_menu, resolve_model_ids
from tools.opnsense_catalog.model_parser import parse_model
from tools.opnsense_catalog.publish import (
    _RELEASE_TAG_RE,
    build_manifest,
    parse_business_base,
    release_versions,
)


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
    cat = build_catalog(models, edition=edition, version=version,
                        generated_from={"core": version})
    fragments = [parse_menu(p.read_text()) for p in discover_menus(source)]
    cat["menu"] = resolve_model_ids(merge_menus(fragments), set(cat["models"]))
    return cat


def _write_catalog(edition: str, version: str, source: Path, out_dir: Path) -> tuple[str, bytes]:
    """Generate one catalog, write community-<version>.json, return (manifest-key, file bytes)."""
    cat = _generate(edition, version, source)
    blob = (json.dumps(cat, indent=2, sort_keys=False) + "\n").encode("utf-8")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{edition}-{version}.json").write_bytes(blob)
    return f"{edition}/{version}", blob


def _generate_plugins(edition: str, version: str, source: Path) -> dict:
    models = []
    for pms in discover_plugin_models(source):
        src = pms.source
        parsed = parse_model(Path(src.model_xml).read_text())
        if not parsed.mount:
            print(f"SKIP {src.module}: model {src.model_xml} has no <mount>", file=sys.stderr)
            continue
        forms = parse_forms([(p.stem, p.read_text()) for p in src.form_paths])
        php = "\n".join(p.read_text() for p in src.controller_paths)
        eps, grid_eps, _conf = resolve_endpoints(src.module, parsed.grids, php or None)
        plugin = {"package": pms.plugin.package, "title": pms.plugin.title,
                  "category": pms.plugin.category, "version": pms.plugin.version}
        m = assemble_model(src.module, parsed, forms, eps, grid_eps, source="plugins", plugin=plugin)
        models.append(m)
    cat = build_catalog(models, edition=edition, version=version,
                        generated_from={"plugins": version})
    fragments = [parse_menu(p.read_text()) for p in discover_menus(source)]
    cat["menu"] = resolve_model_ids(merge_menus(fragments), set(cat["models"]))
    return cat


def _write_plugins_catalog(edition: str, version: str, source: Path, out_dir: Path) -> tuple[str, bytes]:
    """Generate one plugins catalog, write <edition>-plugins-<version>.json, return (manifest-key, bytes)."""
    cat = _generate_plugins(edition, version, source)
    blob = (json.dumps(cat, indent=2, sort_keys=False) + "\n").encode("utf-8")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{edition}-plugins-{version}.json").write_bytes(blob)
    return f"{edition}-plugins/{version}", blob


def _fetch_core_tags() -> list[str]:  # pragma: no cover — network, ops use only
    """All tags of opnsense/core via the GitHub API (paginated). Filtered by release_versions()."""
    import os

    import httpx

    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    if token:  # authenticated => 5000/h instead of 60/h (matters in the scheduled CI publish)
        headers["Authorization"] = f"Bearer {token}"
    tags: list[str] = []
    for page in range(1, 21):  # safety cap: 20 * 100 tags
        r = httpx.get("https://api.github.com/repos/opnsense/core/tags",
                      params={"per_page": 100, "page": page},
                      headers=headers,
                      timeout=30.0, follow_redirects=True)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        tags.extend(t["name"] for t in batch)
    return tags


def _read_changelog_business(changelog_dir: Path) -> dict[str, str]:
    """Read an opnsense/changelog checkout's `business/<major>/<subversion>` files into
    {subversion: text}. Reads EVERY Business release so business-on-business hotfix chains resolve
    transitively (see parse_business_base); skips symlinked majors (older BE series symlink to
    community — no Business 'based on' header) and non-release filenames (e.g. RC `*.r1`). The
    Community catalog floor is enforced downstream by the resolver, not here."""
    pages: dict[str, str] = {}
    business = changelog_dir / "business"
    for major in sorted(business.iterdir()) if business.is_dir() else []:
        if major.is_symlink() or not major.is_dir():
            continue
        for f in sorted(major.iterdir()):
            if _RELEASE_TAG_RE.match(f.name):
                pages[f.name] = f.read_text()
    return pages


def _clone_changelog(dest: Path) -> Path:  # pragma: no cover — network, ops use only
    """Shallow-clone opnsense/changelog into dest; return the checkout path."""
    import subprocess

    subprocess.run(
        ["git", "clone", "--depth", "1", "https://github.com/opnsense/changelog", str(dest)],
        check=True,
    )
    return dest


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
    ga.add_argument("--prior-manifest",
                    help="skip versions already in this manifest (carry their sha) — incremental publish")
    ga.add_argument("--force", action="store_true",
                    help="regenerate every version even if present in --prior-manifest")
    ga.add_argument("--with-plugins", action="store_true",
                    help="also generate a <edition>-plugins-<version>.json per version from opnsense/plugins")
    ga.add_argument("--plugins-source-root",
                    help="dir with one extracted plugins tree per version: <root>/<version>/ (no --fetch)")
    bb = sub.add_parser("business-base")
    bb.add_argument("--changelog-dir", help="path to an opnsense/changelog checkout")
    bb.add_argument("--fetch", action="store_true", help="git clone opnsense/changelog first")
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
        # Incremental: versions already in the prior manifest are skipped (their catalog asset +
        # sha are carried verbatim into the output manifest), unless --force regenerates everything.
        prior: dict[str, str] = {}
        if args.prior_manifest and Path(args.prior_manifest).exists():
            prior = json.loads(Path(args.prior_manifest).read_text()).get("catalogs", {})
        entries: dict[str, bytes] = {}
        carried: dict[str, str] = {}
        skipped: list[str] = []
        for version in versions:
            core_key = f"{args.edition}/{version}"
            plug_key = f"{args.edition}-plugins/{version}"
            need_core = args.force or core_key not in prior
            need_plug = args.with_plugins and (args.force or plug_key not in prior)
            if not need_core and not need_plug:
                if core_key in prior:
                    carried[core_key] = prior[core_key]
                if plug_key in prior:
                    carried[plug_key] = prior[plug_key]
                skipped.append(version)
                continue
            # Core catalog
            if need_core:
                if args.fetch:
                    with tempfile.TemporaryDirectory() as tmp:
                        source = fetch_source("core", version, Path(tmp))
                        key, blob = _write_catalog(args.edition, version, source, out_dir)
                else:
                    source = Path(args.source_root) / version
                    key, blob = _write_catalog(args.edition, version, source, out_dir)
                entries[key] = blob
            elif core_key in prior:
                carried[core_key] = prior[core_key]
            # Plugins catalog
            if need_plug:
                try:
                    if args.fetch:
                        with tempfile.TemporaryDirectory() as tmp:
                            psource = fetch_source("plugins", version, Path(tmp))
                            pkey, pblob = _write_plugins_catalog(args.edition, version, psource, out_dir)
                    else:
                        psource = Path(args.plugins_source_root) / version
                        pkey, pblob = _write_plugins_catalog(args.edition, version, psource, out_dir)
                    entries[pkey] = pblob
                except Exception as exc:  # missing plugins tag for this version, etc. — degrade gracefully
                    print(f"SKIP plugins for {version}: {exc}", file=sys.stderr)
            elif plug_key in prior:
                carried[plug_key] = prior[plug_key]
        manifest = build_manifest(entries)
        manifest["catalogs"].update(carried)  # keep already-published entries in the manifest
        manifest["generated_at"] = datetime.now(UTC).isoformat()
        out_dir.mkdir(parents=True, exist_ok=True)  # may be empty if every version was skipped
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps({"wrote": str(out_dir), "generated": sorted(entries), "skipped": skipped}))
        return 0
    if args.cmd == "list-versions":
        versions = release_versions(_fetch_core_tags(), minimum=args.minimum)
        print(",".join(versions) if args.format == "csv" else "\n".join(versions))
        return 0
    if args.cmd == "business-base":
        if args.fetch:  # pragma: no cover — network, ops use only
            with tempfile.TemporaryDirectory() as tmp:
                pages = _read_changelog_business(_clone_changelog(Path(tmp)))
                data = parse_business_base(pages)
        else:
            data = parse_business_base(_read_changelog_business(Path(args.changelog_dir)))
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
