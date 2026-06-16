# Business→Community base mapping from opnsense/changelog — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate the Business→Community base-version map (`business-base.json`) from the `opnsense/changelog` repo, per **sub-version**, replacing the per-major `docs.opnsense.org` scrape — so a Business device resolves to the most accurate Community base catalog.

**Architecture:** Confined to the offline catalog generator + the publish workflow. `tools/opnsense_catalog/publish.py::parse_business_base` is reused unchanged (its `_BASE_RE` already matches the changelog header). The CLI `business-base` subcommand swaps its input from scraped `BE_<v>.html` to a cloned `opnsense/changelog` `business/` tree. `app/services/catalog_provider.py::resolve_target` is **unchanged** — it already floor-resolves the map keys via `resolve_version`, so a denser (per-sub-version) map flows through the existing logic.

**Tech Stack:** Python 3.14, argparse CLI, pytest. Spec: `docs/superpowers/specs/2026-06-16-business-base-changelog-design.md`.

---

## File structure

| File | Responsibility | Change |
|------|----------------|--------|
| `backend/tools/opnsense_catalog/publish.py` | pure parse of the "based on …" header | docstring only (reuse `parse_business_base`, `_BASE_RE`, `_RELEASE_TAG_RE`, `_version_key`) |
| `backend/tools/opnsense_catalog/cli.py` | `business-base` subcommand | replace `_fetch_business_pages` + `--html-dir`/docs-`--fetch` with `_read_changelog_business` + `--changelog-dir` + git-clone `--fetch` |
| `backend/tests/fixtures/opnsense_catalog/changelog/business/**` | test fixture | new: a tiny `business/<major>/<subver>` tree |
| `backend/tests/fixtures/opnsense_catalog/business/BE_*.html` | old fixture | delete (no longer referenced) |
| `backend/tests/test_catalog_cli.py` | CLI test | replace the `--html-dir` test with a `--changelog-dir` test |
| `backend/tests/test_catalog_provider_resolve.py` | resolver characterization | add per-sub-version exact + floor tests (no app code change) |
| `.github/workflows/publish-catalogs.yml` | publish step | clone `opnsense/changelog`, run `business-base --changelog-dir`, drop docs scrape |

---

## Task 1: Update `parse_business_base` docstring (reuse, no behavior change)

**Files:**
- Modify: `backend/tools/opnsense_catalog/publish.py:20-35`

- [ ] **Step 1: Edit the comment + docstring** (the function body and regex are unchanged — they already match the changelog header).

Replace lines 20-29 (the comment above `_BASE_RE` and the `parse_business_base` docstring) with:

```python
# Both the docs.opnsense.org BE pages and the opnsense/changelog `business/` files state:
# "This business release is based on the OPNsense X.Y.Z community version".
_BASE_RE = re.compile(r"based on the OPNsense\s+(\d+\.\d+(?:\.\d+)?)\s+community", re.IGNORECASE)


def parse_business_base(pages: dict[str, str]) -> dict:
    """pages maps a Business version -> the text of its release notes (an opnsense/changelog
    `business/<major>/<subversion>` file, one entry per sub-version).

    Extracts the Community base version from each entry; entries without the marker are skipped
    (never guess). Returns {"map": {business_version: community_base_version}}.
    """
```

- [ ] **Step 2: Run the existing pure tests to confirm no regression**

Run: `cd backend && python -m pytest tests/test_catalog_publish.py -q`
Expected: PASS (the existing `test_parse_business_base_*` tests are unchanged and still green).

- [ ] **Step 3: Commit**

```bash
git add backend/tools/opnsense_catalog/publish.py
git commit -m "docs(catalog): parse_business_base reads opnsense/changelog business files"
```

---

## Task 2: `_read_changelog_business` + CLI `--changelog-dir`

**Files:**
- Create: `backend/tests/fixtures/opnsense_catalog/changelog/business/26.4/26.4`
- Create: `backend/tests/fixtures/opnsense_catalog/changelog/business/26.4/26.4.1`
- Create: `backend/tests/fixtures/opnsense_catalog/changelog/business/26.4/26.4.r1`
- Create: `backend/tests/fixtures/opnsense_catalog/changelog/business/25.10/25.10`
- Create: `backend/tests/fixtures/opnsense_catalog/changelog/business/20.1/20.1`
- Modify: `backend/tests/test_catalog_cli.py:61-72`
- Modify: `backend/tools/opnsense_catalog/cli.py` (remove `_fetch_business_pages`; add `_read_changelog_business`; rewrite the `business-base` subcommand + dispatch)
- Delete: `backend/tests/fixtures/opnsense_catalog/business/` (old `BE_*.html`)

- [ ] **Step 1: Create the changelog fixture tree** (real header phrasing; an RC and a below-floor file to prove filtering)

`backend/tests/fixtures/opnsense_catalog/changelog/business/26.4/26.4`:
```
@ March 1, 2026

This business release is based on the OPNsense 26.1.6 community version with
additional reliability improvements.
```

`backend/tests/fixtures/opnsense_catalog/changelog/business/26.4/26.4.1`:
```
@ June 16, 2026

This business release is based on the OPNsense 26.1.9 community version with
additional reliability improvements.
```

`backend/tests/fixtures/opnsense_catalog/changelog/business/26.4/26.4.r1` (release candidate — no "based on" line):
```
@ February 1, 2026

Business 26.4 release candidate. Testing only.
```

`backend/tests/fixtures/opnsense_catalog/changelog/business/25.10/25.10`:
```
@ October 1, 2025

This business release is based on the OPNsense 25.7.5 community version with
additional reliability improvements.
```

`backend/tests/fixtures/opnsense_catalog/changelog/business/20.1/20.1` (below the floor — must be ignored):
```
@ January 1, 2020

This business release is based on the OPNsense 20.1.1 community version.
```

- [ ] **Step 2: Write the failing CLI test** — replace the existing `test_business_base_writes_map_from_html_dir` (lines 61-72 of `tests/test_catalog_cli.py`) with:

```python
_CHANGELOG = Path(__file__).parent / "fixtures/opnsense_catalog/changelog"


def test_business_base_writes_map_from_changelog_dir(tmp_path):
    out = tmp_path / "business-base.json"
    rc = main(["business-base", "--changelog-dir", str(_CHANGELOG), "--out", str(out)])
    assert rc == 0
    data = json.loads(out.read_text())
    # Per-sub-version keys; the RC (no header) and the below-floor 20.1 are dropped.
    assert data["map"] == {"26.4": "26.1.6", "26.4.1": "26.1.9", "25.10": "25.7.5"}
    assert "generated_at" in data
```

(Keep the file's existing imports — `json`, `Path`, `main` are already imported at the top.)

- [ ] **Step 3: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_catalog_cli.py::test_business_base_writes_map_from_changelog_dir -v`
Expected: FAIL — `error: unrecognized arguments: --changelog-dir` (the subcommand still has `--html-dir`).

- [ ] **Step 4: Implement the reader + rewire the subcommand**

In `backend/tools/opnsense_catalog/cli.py`:

(a) Add the import of the reuse helpers near the top (alongside the existing `from tools.opnsense_catalog.publish import build_manifest, parse_business_base, release_versions`):

```python
from tools.opnsense_catalog.publish import (
    build_manifest,
    parse_business_base,
    release_versions,
)
from tools.opnsense_catalog.publish import _RELEASE_TAG_RE, _version_key
```

(b) **Delete** `_fetch_business_pages()` (lines 104-119) and **replace** it with:

```python
_BUSINESS_FLOOR = (25,)  # match the Community catalog floor (--minimum 25); ignore ancient BE series


def _read_changelog_business(changelog_dir: Path) -> dict[str, str]:
    """Read an opnsense/changelog checkout's `business/<major>/<subversion>` files into
    {subversion: text}. Skips symlinked majors (older BE series symlink to community), pre-floor
    majors, and non-release filenames (e.g. release candidates `*.r1`)."""
    pages: dict[str, str] = {}
    business = changelog_dir / "business"
    for major in sorted(business.iterdir()) if business.is_dir() else []:
        if major.is_symlink() or not major.is_dir():
            continue
        for f in sorted(major.iterdir()):
            name = f.name
            if not _RELEASE_TAG_RE.match(name) or _version_key(name) < _BUSINESS_FLOOR:
                continue
            pages[name] = f.read_text()
    return pages


def _clone_changelog(dest: Path) -> Path:  # pragma: no cover — network, ops use only
    """Shallow-clone opnsense/changelog into dest; return the checkout path."""
    import subprocess

    subprocess.run(
        ["git", "clone", "--depth", "1", "https://github.com/opnsense/changelog", str(dest)],
        check=True,
    )
    return dest
```

(c) **Replace** the `business-base` subparser (lines 148-151) with:

```python
    bb = sub.add_parser("business-base")
    bb.add_argument("--changelog-dir", help="path to an opnsense/changelog checkout")
    bb.add_argument("--fetch", action="store_true", help="git clone opnsense/changelog first")
    bb.add_argument("--out", required=True)
```

(d) **Replace** the `business-base` dispatch (lines 226-237) with:

```python
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
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_catalog_cli.py -q`
Expected: PASS (the new `--changelog-dir` test green; no other CLI test referenced `business-base`).

- [ ] **Step 6: Delete the now-unused old fixture**

```bash
git rm -r backend/tests/fixtures/opnsense_catalog/business
```

(Confirm nothing else references it: `grep -rn "fixtures/opnsense_catalog/business\b" backend/tests` returns nothing.)

- [ ] **Step 7: Commit**

```bash
git add backend/tools/opnsense_catalog/cli.py backend/tests/test_catalog_cli.py \
        backend/tests/fixtures/opnsense_catalog/changelog
git commit -m "feat(catalog): business-base reads opnsense/changelog (per-sub-version, --changelog-dir)"
```

---

## Task 3: Resolver characterization — per-sub-version map (no app change)

**Files:**
- Modify: `backend/tests/test_catalog_provider_resolve.py` (append)

This locks in the spec's key finding: `resolve_target` already serves a denser map correctly. The tests pass without any change to `app/services/catalog_provider.py`.

- [ ] **Step 1: Append the tests**

```python
# A per-sub-version Business map (as opnsense/changelog now produces) resolves more precisely than
# the old per-major map — exact sub-version hit, and floor for an unknown newer sub-version.
_BIZ_SUBVER = {"map": {"26.4": "26.1.6", "26.4.1": "26.1.8", "25.10": "25.7.9"}}


def test_resolve_target_business_subversion_exact():
    # BE 26.4.1's own base (26.1.8) is served, not the major 26.4 base (26.1.6).
    assert resolve_target(_MANIFEST, _BIZ_SUBVER, "business", "26.4.1") == ("community", "26.1.8")


def test_resolve_target_business_subversion_floor():
    # An unknown newer BE sub-version floors to the nearest known sub-version (26.4.1 -> 26.1.8).
    assert resolve_target(_MANIFEST, _BIZ_SUBVER, "business", "26.4.2") == ("community", "26.1.8")


def test_resolve_target_business_major_still_works():
    # Backward compat: a per-major-only map (the old shape) still resolves.
    assert resolve_target(_MANIFEST, _BIZ, "business", "26.4") == ("community", "26.1.6")
```

- [ ] **Step 2: Run to verify they pass immediately** (proving no app change is needed)

Run: `cd backend && python -m pytest tests/test_catalog_provider_resolve.py -q`
Expected: PASS for all (existing + 3 new). If `test_resolve_target_business_subversion_exact` FAILS, the resolver does not floor over BE keys and the spec's "no app change" claim is wrong — STOP and escalate (do not silently add resolver code).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_catalog_provider_resolve.py
git commit -m "test(catalog): resolve_target serves per-sub-version Business maps (floor)"
```

---

## Task 4: Publish workflow — clone the changelog, drop the docs scrape

**Files:**
- Modify: `.github/workflows/publish-catalogs.yml` (the `business-base` step)

- [ ] **Step 1: Find the current business-base step**

Run: `grep -n "business-base\|business-base.json\|docs.opnsense\|--html-dir\|--fetch" .github/workflows/publish-catalogs.yml`
Read the step that produces `business-base.json` and note its exact YAML (the `run:` block and where the file is uploaded).

- [ ] **Step 2: Replace the step's `run:` body** so it clones the changelog and uses `--changelog-dir` instead of the docs scrape. The new `run:` (keep the step's `name:`, `working-directory: backend`, and any surrounding upload step unchanged):

```yaml
        run: |
          rm -rf /tmp/opnsense-changelog
          git clone --depth 1 https://github.com/opnsense/changelog /tmp/opnsense-changelog
          python -m tools.opnsense_catalog.cli business-base \
            --changelog-dir /tmp/opnsense-changelog \
            --out "$RUNNER_TEMP/business-base.json"
```

(Match the existing `--out` path / env var the workflow already uses for `business-base.json`; if the prior step wrote to a different path, keep that path. Do **not** change the upload-to-release step.)

- [ ] **Step 3: Validate the YAML parses**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/publish-catalogs.yml')); print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/publish-catalogs.yml
git commit -m "ci(catalogs): build business-base.json from opnsense/changelog clone"
```

---

## Task 5: Gate

- [ ] **Step 1: Full backend suite + lint**

Run: `cd backend && python -m pytest -q && ruff check app/ tools/`
Expected: all tests pass; ruff clean.

- [ ] **Step 2: Commit any lint fixups** (only if ruff changed anything)

```bash
git add -A && git commit -m "chore: ruff"
```

---

## Verification (operator, no box — after merge or pre-PR)

Run the CLI against a real clone and eyeball the map:

```bash
cd backend
rm -rf /tmp/cl && git clone --depth 1 https://github.com/opnsense/changelog /tmp/cl
python -m tools.opnsense_catalog.cli business-base --changelog-dir /tmp/cl --out /tmp/bb.json
python -c "import json; m=json.load(open('/tmp/bb.json'))['map']; print(len(m), m.get('26.4.1'), m.get('25.10'))"
```

Expected: a per-sub-version map (dozens of keys), with `26.4.1 -> 26.1.9` and `25.10 -> 25.7.5` (the real bases). The publish Action republishes the denser `business-base.json` on its next 6-hourly run.

---

## Self-review (plan vs spec)

- **Spec coverage:** changelog source replaces docs scrape (Task 2 + Task 4) ✓; `parse_business_base` reused (Task 1) ✓; per-sub-version keys + floor (Task 2 fixture + Task 3 tests) ✓; no app/resolver change, proven by characterization tests (Task 3) ✓; never-guess / RC + below-floor skipped (Task 2 fixture `26.4.r1`, `20.1`) ✓; tests + gate (Tasks 2/3/5) ✓; verification (no box) ✓; proprietary-plugin schemas out of scope (untouched) ✓.
- **Placeholder scan:** none — every code step shows the full code; the only "match the existing path" note (Task 4) is an explicit instruction to read the current YAML, not a placeholder.
- **Type/name consistency:** `_read_changelog_business`, `_clone_changelog`, `_BUSINESS_FLOOR`, `--changelog-dir`, `parse_business_base`, `_RELEASE_TAG_RE`, `_version_key` used consistently; map shape `{"map": {subver: ce_base}}` identical across tasks; resolver untouched.
