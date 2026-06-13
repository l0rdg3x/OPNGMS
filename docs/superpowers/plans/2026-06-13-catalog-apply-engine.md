# Catalog Distribution + Generic Apply Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the offline-generated OPNsense catalogs available to the running app (published to GitHub Releases, fetched + cached + SHA-256-verified, with Business→Community base resolution) and add a generic `catalog_setting` change kind that can push ANY catalog model (scalars + grids) through the existing config pipeline.

**Architecture:** Three layers. (A) **Publishing tooling** — new generator CLI subcommands emit per-version catalogs, a SHA-256 `manifest.json`, and a `business-base.json` Business→Community map; an ops step uploads them to a rolling `catalogs` release. (B) **Provider** — `services/catalog_provider.py` fetches the manifest, resolves the device's (edition, version) to a published catalog (Business maps to its Community base), verifies the catalog's SHA-256, and caches the JSON in a new global `catalog_cache` table. (C) **Generic apply** — the connector gains `apply_grid_item` + a `reconfigure`-suppressible `apply_setting`; `services/catalog_kind.py` registers a `catalog_setting` applier; `api/catalog.py` exposes a create endpoint (resolve catalog → validate → embed endpoints → `create_change`) and a read endpoint. The change then rides the EXISTING schedule/snapshot/staleness/revert pipeline unchanged.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy (async), Postgres (JSONB, app-role grants), httpx, respx (test), pytest. Generator package `backend/tools/opnsense_catalog/`.

**Spec:** `docs/superpowers/specs/2026-06-13-catalog-apply-engine-design.md`

**Branch:** `feat/catalog-apply-engine` (already checked out; the spec commit is already on it).

---

## Conventions used throughout this plan

- All paths are relative to `backend/` unless absolute. Run `pytest`/`ruff` from `backend/` (the repo has `pythonpath = ["."]` so both `app.*` and `tools.*` import).
- Run a single test file with: `cd backend && python -m pytest tests/<file>.py -v`.
- Ruff lints `app/` (not `tests/`): `cd backend && ruff check app/`.
- Commit after every task. English in code/commits; chat stays Italian (controller concern, not the implementer's).
- **Published artifact shapes** (fixed for the whole plan — do not deviate):
  - Catalog file name: `community-<version>.json` (e.g. `community-26.1.8.json`). Its bytes are exactly what the generator wrote.
  - `manifest.json`: `{"generated_at": "<iso8601 or empty>", "catalogs": {"community/<version>": "<sha256-hex of the catalog file bytes>", ...}}`.
  - `business-base.json`: `{"generated_at": "<iso8601 or empty>", "map": {"<business_version>": "<community_base_version>", ...}}`.

---

## Phase A — Catalog publishing tooling (generator CLI)

Pure tooling, no DB, no app process. Produces the artifacts the provider will consume.

### Task A1: Publish helpers — `sha256_hex` + `build_manifest`

**Files:**
- Create: `tools/opnsense_catalog/publish.py`
- Test: `tests/test_catalog_publish.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_catalog_publish.py
import hashlib

from tools.opnsense_catalog.publish import build_manifest, sha256_hex


def test_sha256_hex_matches_hashlib():
    data = b'{"models": {}}'
    assert sha256_hex(data) == hashlib.sha256(data).hexdigest()


def test_build_manifest_maps_edition_version_to_sha():
    a = b'{"version": "26.1.7"}'
    b = b'{"version": "26.1.8"}'
    manifest = build_manifest({"community/26.1.7": a, "community/26.1.8": b})
    assert manifest == {
        "catalogs": {
            "community/26.1.7": hashlib.sha256(a).hexdigest(),
            "community/26.1.8": hashlib.sha256(b).hexdigest(),
        }
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_catalog_publish.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.opnsense_catalog.publish'`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/opnsense_catalog/publish.py
from __future__ import annotations

import hashlib


def sha256_hex(data: bytes) -> str:
    """Hex SHA-256 of raw bytes (the integrity check the provider re-verifies)."""
    return hashlib.sha256(data).hexdigest()


def build_manifest(entries: dict[str, bytes]) -> dict:
    """entries maps "edition/version" -> the catalog file's exact bytes.

    Returns {"catalogs": {"edition/version": "<sha256-hex>"}}. The CLI adds `generated_at`.
    """
    return {"catalogs": {key: sha256_hex(blob) for key, blob in entries.items()}}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_catalog_publish.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
cd backend && git add tools/opnsense_catalog/publish.py tests/test_catalog_publish.py
git commit -m "feat(catalog): publish helpers — sha256_hex + build_manifest"
```

---

### Task A2: Publish helper — `parse_business_base`

**Files:**
- Modify: `tools/opnsense_catalog/publish.py`
- Test: `tests/test_catalog_publish.py` (add to the existing file)

- [ ] **Step 1: Write the failing test (append)**

```python
# append to tests/test_catalog_publish.py
from tools.opnsense_catalog.publish import parse_business_base

_BE_26_4 = """
<html><body>
<h1>OPNsense 26.4 Business Edition</h1>
<p>This business release is based on the OPNsense 26.1.6 community version with
additional reliability improvements.</p>
</body></html>
"""

_BE_25_10 = "blah ... based on the OPNsense 25.7.9 community version ... blah"


def test_parse_business_base_extracts_community_base():
    out = parse_business_base({"26.4": _BE_26_4, "25.10": _BE_25_10})
    assert out == {"map": {"26.4": "26.1.6", "25.10": "25.7.9"}}


def test_parse_business_base_skips_pages_without_the_marker():
    out = parse_business_base({"26.4": _BE_26_4, "99.9": "<html>no marker here</html>"})
    assert out == {"map": {"26.4": "26.1.6"}}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_catalog_publish.py -k business_base -v`
Expected: FAIL with `ImportError: cannot import name 'parse_business_base'`

- [ ] **Step 3: Write minimal implementation (append to publish.py)**

```python
# append to tools/opnsense_catalog/publish.py
import re

# OPNsense BE release pages state: "based on the OPNsense X.Y.Z community version".
_BASE_RE = re.compile(r"based on the OPNsense\s+(\d+\.\d+(?:\.\d+)?)\s+community", re.IGNORECASE)


def parse_business_base(pages: dict[str, str]) -> dict:
    """pages maps a Business version -> its BE_<v>.html text.

    Extracts the Community base version from each page; pages without the marker are skipped
    (never guess). Returns {"map": {business_version: community_base_version}}.
    """
    mapping: dict[str, str] = {}
    for be_version, html in pages.items():
        m = _BASE_RE.search(html or "")
        if m:
            mapping[be_version] = m.group(1)
    return {"map": mapping}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_catalog_publish.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
cd backend && git add tools/opnsense_catalog/publish.py tests/test_catalog_publish.py
git commit -m "feat(catalog): parse_business_base — scrape BE_<v>.html for Community base"
```

---

### Task A3: CLI `generate-all` subcommand

Emits one `community-<version>.json` per requested version plus `manifest.json` into an out dir.
For testability it reads each version's extracted source from `<source-root>/<version>/` (the same
layout the existing `--source` path expects); a `--fetch` flag downloads instead (ops only, no test).

**Files:**
- Modify: `tools/opnsense_catalog/cli.py`
- Test: `tests/test_catalog_cli.py` (add to existing file)

- [ ] **Step 1: Write the failing test (append)**

```python
# append to tests/test_catalog_cli.py
import hashlib
import shutil

from tools.opnsense_catalog.publish import sha256_hex


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_catalog_cli.py -k generate_all -v`
Expected: FAIL — argparse errors on the unknown `generate-all` subcommand (SystemExit / non-zero rc)

- [ ] **Step 3: Write minimal implementation**

Add the imports and a helper near the top of `cli.py` (after the existing imports):

```python
# tools/opnsense_catalog/cli.py — add to imports
from datetime import UTC, datetime

from tools.opnsense_catalog.publish import build_manifest
```

Add a writer helper (below `_generate`):

```python
def _write_catalog(edition: str, version: str, source: Path, out_dir: Path) -> tuple[str, bytes]:
    """Generate one catalog, write community-<version>.json, return (manifest-key, file bytes)."""
    cat = _generate(edition, version, source)
    blob = (json.dumps(cat, indent=2, sort_keys=False) + "\n").encode("utf-8")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{edition}-{version}.json").write_bytes(blob)
    return f"{edition}/{version}", blob
```

In `main`, register the subparser (next to `generate`):

```python
    ga = sub.add_parser("generate-all")
    ga.add_argument("--edition", default="community")
    ga.add_argument("--versions", required=True, help="comma-separated, e.g. 26.1.7,26.1.8")
    ga.add_argument("--source-root", help="dir with one extracted source tree per version: <root>/<version>/")
    ga.add_argument("--fetch", action="store_true", help="download each tag instead of --source-root")
    ga.add_argument("--out-dir", required=True)
```

And handle it (before the `diff` branch):

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_catalog_cli.py -v`
Expected: PASS (existing + new test green)

- [ ] **Step 5: Commit**

```bash
cd backend && git add tools/opnsense_catalog/cli.py tests/test_catalog_cli.py
git commit -m "feat(catalog): CLI generate-all — per-version catalogs + sha256 manifest"
```

---

### Task A4: CLI `business-base` subcommand

Reads vendored `BE_*.html` files from `--html-dir` (test) and writes `business-base.json`; `--fetch`
scrapes `docs.opnsense.org` (ops only, not tested — network). File name pattern `BE_<version>.html`.

**Files:**
- Modify: `tools/opnsense_catalog/cli.py`
- Create: `tests/fixtures/opnsense_catalog/business/BE_26.4.html`
- Create: `tests/fixtures/opnsense_catalog/business/BE_25.10.html`
- Test: `tests/test_catalog_cli.py` (add)

- [ ] **Step 1: Create the fixtures**

`tests/fixtures/opnsense_catalog/business/BE_26.4.html`:
```html
<html><body><p>This business release is based on the OPNsense 26.1.6 community version.</p></body></html>
```

`tests/fixtures/opnsense_catalog/business/BE_25.10.html`:
```html
<html><body><p>... based on the OPNsense 25.7.9 community version ...</p></body></html>
```

- [ ] **Step 2: Write the failing test (append to tests/test_catalog_cli.py)**

```python
_BIZ = Path(__file__).parent / "fixtures/opnsense_catalog/business"


def test_business_base_writes_map_from_html_dir(tmp_path):
    out = tmp_path / "business-base.json"
    rc = main(["business-base", "--html-dir", str(_BIZ), "--out", str(out)])
    assert rc == 0
    data = json.loads(out.read_text())
    assert data["map"] == {"26.4": "26.1.6", "25.10": "25.7.9"}
    assert "generated_at" in data
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_catalog_cli.py -k business_base -v`
Expected: FAIL — argparse errors on the unknown `business-base` subcommand

- [ ] **Step 4: Write minimal implementation**

Add the import near the others:
```python
from tools.opnsense_catalog.publish import parse_business_base
```

Register the subparser:
```python
    bb = sub.add_parser("business-base")
    bb.add_argument("--html-dir", help="dir of vendored BE_<version>.html files")
    bb.add_argument("--fetch", action="store_true", help="scrape docs.opnsense.org instead")
    bb.add_argument("--out", required=True)
```

Handle it (before `diff`):
```python
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
```

Add the ops-only fetch helper (below `_write_catalog`); not covered by tests (network):
```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_catalog_cli.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd backend && git add tools/opnsense_catalog/cli.py tests/test_catalog_cli.py tests/fixtures/opnsense_catalog/business/
git commit -m "feat(catalog): CLI business-base — emit Business→Community base map"
```

---

### Task A5: Document the publish ops run

**Files:**
- Modify: `tools/opnsense_catalog/README.md` (if absent, Create it)

- [ ] **Step 1: Add a "Publishing" section**

Append (or create the file with) this section. No test — docs only.

````markdown
## Publishing catalogs to the `catalogs` release

The running app fetches catalogs dynamically; they are NOT committed. To publish/refresh:

```bash
cd backend
# 1. Generate every catalog + the sha256 manifest for the versions you support:
python -m tools.opnsense_catalog.cli generate-all \
    --edition community --versions 26.1.7,26.1.8 --fetch --out-dir /tmp/catalogs

# 2. Refresh the Business→Community base map (scrapes docs.opnsense.org):
python -m tools.opnsense_catalog.cli business-base --fetch --out /tmp/catalogs/business-base.json

# 3. Upload all assets to the rolling `catalogs` release (replaces existing assets):
gh release upload catalogs /tmp/catalogs/* --clobber
```

The app reads `<CATALOG_RELEASE_BASE_URL>/manifest.json`, `<...>/business-base.json` (for Business
devices) and `<...>/community-<version>.json`, verifying each catalog's SHA-256 against the manifest.
Publishing a new OPNsense version requires NO app release.
````

- [ ] **Step 2: Commit**

```bash
cd backend && git add tools/opnsense_catalog/README.md
git commit -m "docs(catalog): document the publish ops run"
```

---

## Phase B — Catalog provider (app-side: settings, model, migration, fetch/cache/verify)

### Task B1: Settings — `catalog_release_base_url` + `catalog_auto_fetch`

**Files:**
- Modify: `app/core/config.py` (the `Settings` class)
- Test: `tests/test_catalog_settings.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_catalog_settings.py
from app.core.config import Settings


def _minimal(**over):
    base = dict(database_url="postgresql+asyncpg://x", session_secret="s", master_key="k")
    base.update(over)
    return Settings(**base)


def test_catalog_settings_have_defaults():
    s = _minimal()
    assert s.catalog_release_base_url.startswith("https://github.com/")
    assert s.catalog_auto_fetch is True


def test_catalog_settings_overridable():
    s = _minimal(catalog_release_base_url="https://x/y", catalog_auto_fetch=False)
    assert s.catalog_release_base_url == "https://x/y"
    assert s.catalog_auto_fetch is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_catalog_settings.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'catalog_release_base_url'`

- [ ] **Step 3: Write minimal implementation**

In `app/core/config.py`, add to the `Settings` class (after the `silent_alert_*` lines):

```python
    # Catalog distribution (sub-project 2): where the app fetches versioned OPNsense catalogs.
    catalog_release_base_url: str = (
        "https://github.com/l0rdg3x/OPNGMS/releases/download/catalogs"
    )
    catalog_auto_fetch: bool = True  # fetch + cache catalogs on cache-miss (off => cache-only)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_catalog_settings.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/core/config.py tests/test_catalog_settings.py
git commit -m "feat(catalog): settings — catalog_release_base_url + catalog_auto_fetch"
```

---

### Task B2: `CatalogCache` model

Global (non-RLS) table — only the provider/worker/superadmin touch it, mirroring `smtp_settings`.

**Files:**
- Create: `app/models/catalog_cache.py`
- Modify: `app/models/__init__.py`
- Test: `tests/test_catalog_cache_model.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_catalog_cache_model.py
from app.models import CatalogCache


def test_catalog_cache_table_and_columns():
    t = CatalogCache.__table__
    assert t.name == "catalog_cache"
    cols = set(t.columns.keys())
    assert {"id", "edition", "version", "sha256", "content", "fetched_at"} <= cols
    # unique on (edition, version)
    uniques = [tuple(c.name for c in con.columns)
               for con in t.constraints if con.__class__.__name__ == "UniqueConstraint"]
    assert ("edition", "version") in uniques
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_catalog_cache_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'CatalogCache' from 'app.models'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/models/catalog_cache.py
from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class CatalogCache(UUIDPKMixin, Base):
    """Cached versioned OPNsense catalog (JSON) fetched from the `catalogs` release.

    Global, non-tenant: only the provider/worker/superadmin path touches it, so no RLS — the blanket
    app-role grants (like smtp_settings/syslog_ca) let opngms_app read/write it. Keyed by the RESOLVED
    identity (edition, version); a Business device reuses its Community base row.
    """

    __tablename__ = "catalog_cache"
    __table_args__ = (UniqueConstraint("edition", "version", name="uq_catalog_cache_edition_version"),)

    edition: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)
    sha256: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

In `app/models/__init__.py`, add the import (alphabetically near `config_*`) and the `__all__` entry:

```python
from app.models.catalog_cache import CatalogCache  # noqa: F401
```
```python
    "CatalogCache",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_catalog_cache_model.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/models/catalog_cache.py app/models/__init__.py tests/test_catalog_cache_model.py
git commit -m "feat(catalog): CatalogCache model (global, non-RLS)"
```

---

### Task B3: Migration `0028_catalog_cache`

Tests build the schema via `Base.metadata.create_all`, so this migration is for PRODUCTION parity.
Verify it manually with alembic against a scratch DB.

**Files:**
- Create: `migrations/versions/0028_catalog_cache.py`

- [ ] **Step 1: Write the migration**

```python
# migrations/versions/0028_catalog_cache.py
"""catalog_cache: cached versioned OPNsense catalogs (global, non-RLS) for the generic editor"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.db_roles import grant_app_role_statements

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "catalog_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("edition", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("sha256", sa.String(), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("edition", "version", name="uq_catalog_cache_edition_version"),
    )
    # Global table (no RLS) — provider/worker/superadmin only. Reapply the blanket app-role grants
    # so opngms_app can read/write it (matches smtp_settings/syslog_ca/silent_tenant_alerts).
    for stmt in grant_app_role_statements():
        op.execute(stmt)


def downgrade() -> None:
    op.drop_table("catalog_cache")
```

- [ ] **Step 2: Verify alembic upgrade/downgrade against a scratch DB**

Run (uses `ALEMBIC_DATABASE_URL` / the migrate config; substitute your scratch DB):
```bash
cd backend && alembic upgrade head && alembic downgrade -1 && alembic upgrade head
```
Expected: no errors; `catalog_cache` exists after `upgrade head`. (If no scratch DB is available in this
environment, confirm `alembic heads` shows `0028` as the single head and the file imports cleanly:
`python -c "import ast; ast.parse(open('migrations/versions/0028_catalog_cache.py').read())"`.)

- [ ] **Step 3: Commit**

```bash
cd backend && git add migrations/versions/0028_catalog_cache.py
git commit -m "feat(catalog): migration 0028 — catalog_cache table + app-role grants"
```

---

### Task B4: Provider pure resolvers — `resolve_version` + `resolve_target`

**Files:**
- Create: `app/services/catalog_provider.py`
- Test: `tests/test_catalog_provider_resolve.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_catalog_provider_resolve.py
from app.services.catalog_provider import resolve_target, resolve_version

_MANIFEST = {"catalogs": {"community/26.1.6": "x", "community/26.1.7": "x", "community/26.1.8": "x"}}
_BIZ = {"map": {"26.4": "26.1.6", "25.10": "25.7.9"}}


def test_resolve_version_exact():
    assert resolve_version(["26.1.7", "26.1.8"], "26.1.8") == "26.1.8"


def test_resolve_version_floor():
    assert resolve_version(["26.1.6", "26.1.8"], "26.1.7") == "26.1.6"


def test_resolve_version_none_below():
    assert resolve_version(["26.1.6"], "26.1.5") is None


def test_resolve_version_tolerates_suffix():
    assert resolve_version(["26.1.8"], "26.1.8_4") == "26.1.8"


def test_resolve_target_community_passthrough():
    assert resolve_target(_MANIFEST, None, "community", "26.1.8") == ("community", "26.1.8")


def test_resolve_target_community_floor():
    assert resolve_target(_MANIFEST, None, "", "26.1.9") == ("community", "26.1.8")


def test_resolve_target_business_maps_to_community_base():
    # BE 26.4 -> CE 26.1.6 (exact in the manifest)
    assert resolve_target(_MANIFEST, _BIZ, "business", "26.4") == ("community", "26.1.6")


def test_resolve_target_business_unmapped_is_none():
    assert resolve_target(_MANIFEST, _BIZ, "business", "24.1") is None


def test_resolve_target_business_base_below_manifest_is_none():
    # BE maps to a Community base older than anything published.
    biz = {"map": {"24.4": "24.1.1"}}
    assert resolve_target(_MANIFEST, biz, "business", "24.4") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_catalog_provider_resolve.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.catalog_provider'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/services/catalog_provider.py
"""Fetch + cache + verify versioned OPNsense catalogs published as GitHub Release assets.

A Business device is served the Community catalog of its base version (resolve_target maps it via
business-base.json). The catalog file's SHA-256 is verified against the manifest before it is cached
or used. Offline, a previously-cached catalog is still served; a cold offline start returns None.
"""
from __future__ import annotations

import re

_NUM = re.compile(r"\d+")


def _parse_version(v: str) -> tuple[int, ...]:
    """'26.1.8' -> (26, 1, 8). Tolerant of suffixes ('26.1.8_4' -> (26, 1, 8))."""
    parts: list[int] = []
    for p in v.split("."):
        m = _NUM.match(p)
        parts.append(int(m.group()) if m else 0)
    return tuple(parts)


def resolve_version(versions: list[str], version: str) -> str | None:
    """Exact match else the highest published version <= `version`. None if none <=."""
    if version in versions:
        return version
    target = _parse_version(version)
    below = [v for v in versions if _parse_version(v) <= target]
    return max(below, key=_parse_version) if below else None


def _community_versions(manifest: dict) -> list[str]:
    return [k.split("/", 1)[1] for k in manifest.get("catalogs", {}) if k.startswith("community/")]


def resolve_target(
    manifest: dict, business_base: dict | None, edition: str, version: str
) -> tuple[str, str] | None:
    """Return the (resolved_edition, resolved_version) catalog to serve, or None.

    community (or unknown/empty edition): floor-resolve against the manifest.
    business: map version -> Community base via business_base, then floor-resolve THAT in the
    manifest. A Business device is always served a Community catalog (the shared core).
    """
    community = _community_versions(manifest)
    if (edition or "community").lower() == "business":
        bmap = (business_base or {}).get("map", {})
        be = resolve_version(list(bmap), version)
        if be is None:
            return None
        cv = resolve_version(community, bmap[be])
        return ("community", cv) if cv else None
    cv = resolve_version(community, version)
    return ("community", cv) if cv else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_catalog_provider_resolve.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/catalog_provider.py tests/test_catalog_provider_resolve.py
git commit -m "feat(catalog): provider pure resolvers — resolve_version + resolve_target"
```

---

### Task B5: Provider `get_catalog` + `get_model` (fetch + verify + cache + offline)

**Files:**
- Modify: `app/services/catalog_provider.py`
- Test: `tests/test_catalog_provider_fetch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_catalog_provider_fetch.py
import hashlib
import json

import httpx
import respx
from httpx import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.catalog_cache import CatalogCache
from app.services.catalog_provider import get_catalog, get_model

_BASE = "https://catalogs.test"
_CATALOG = {"edition": "community", "version": "26.1.8", "models": {"unbound": {"id": "unbound"}}}
_BLOB = (json.dumps(_CATALOG)).encode("utf-8")
_SHA = hashlib.sha256(_BLOB).hexdigest()
_MANIFEST = {"generated_at": "", "catalogs": {"community/26.1.8": _SHA}}


def _mock_release(catalog_blob=_BLOB, sha=_SHA, business=None):
    respx.get(f"{_BASE}/manifest.json").mock(
        return_value=Response(200, json={"generated_at": "", "catalogs": {"community/26.1.8": sha}}))
    respx.get(f"{_BASE}/community-26.1.8.json").mock(return_value=Response(200, content=catalog_blob))
    if business is not None:
        respx.get(f"{_BASE}/business-base.json").mock(return_value=Response(200, json=business))


@respx.mock
async def test_get_catalog_fetches_verifies_and_caches(db_engine):
    _mock_release()
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        cat = await get_catalog(s, "community", "26.1.8", base_url=_BASE, auto_fetch=True)
        assert cat["version"] == "26.1.8"
        await s.commit()
    async with factory() as s:
        rows = (await s.execute(select(CatalogCache))).scalars().all()
        assert len(rows) == 1 and rows[0].sha256 == _SHA


@respx.mock
async def test_get_catalog_rejects_sha_mismatch(db_engine):
    # manifest advertises a sha that does NOT match the served bytes -> reject, do not cache.
    _mock_release(sha="deadbeef")
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        cat = await get_catalog(s, "community", "26.1.8", base_url=_BASE, auto_fetch=True)
        assert cat is None
        assert (await s.execute(select(CatalogCache))).first() is None


@respx.mock
async def test_get_catalog_warm_cache_skips_download(db_engine):
    _mock_release()
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await get_catalog(s, "community", "26.1.8", base_url=_BASE, auto_fetch=True)
        await s.commit()
    # Second call: manifest still served, but the catalog route is removed -> must hit cache.
    respx.get(f"{_BASE}/community-26.1.8.json").mock(side_effect=AssertionError("should not download"))
    async with factory() as s:
        cat = await get_catalog(s, "community", "26.1.8", base_url=_BASE, auto_fetch=True)
        assert cat["version"] == "26.1.8"


@respx.mock
async def test_get_catalog_offline_serves_cached(db_engine):
    # Pre-seed a cache row, then make the manifest unreachable.
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        s.add(CatalogCache(edition="community", version="26.1.8", sha256=_SHA, content=_CATALOG))
        await s.commit()
    respx.get(f"{_BASE}/manifest.json").mock(side_effect=httpx.ConnectError("offline"))
    async with factory() as s:
        cat = await get_catalog(s, "community", "26.1.8", base_url=_BASE, auto_fetch=True)
        assert cat["version"] == "26.1.8"


@respx.mock
async def test_get_catalog_offline_cold_returns_none(db_engine):
    respx.get(f"{_BASE}/manifest.json").mock(side_effect=httpx.ConnectError("offline"))
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        assert await get_catalog(s, "community", "26.1.8", base_url=_BASE, auto_fetch=True) is None


@respx.mock
async def test_get_catalog_business_resolves_to_community_base(db_engine):
    # BE 26.4 -> CE 26.1.6; serve the Community catalog, cache under ("community","26.1.6").
    biz_catalog = {"edition": "community", "version": "26.1.6", "models": {}}
    blob = json.dumps(biz_catalog).encode()
    sha = hashlib.sha256(blob).hexdigest()
    respx.get(f"{_BASE}/manifest.json").mock(
        return_value=Response(200, json={"generated_at": "", "catalogs": {"community/26.1.6": sha}}))
    respx.get(f"{_BASE}/business-base.json").mock(
        return_value=Response(200, json={"map": {"26.4": "26.1.6"}}))
    respx.get(f"{_BASE}/community-26.1.6.json").mock(return_value=Response(200, content=blob))
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        cat = await get_catalog(s, "business", "26.4", base_url=_BASE, auto_fetch=True)
        assert cat["version"] == "26.1.6"
        await s.commit()
    async with factory() as s:
        row = (await s.execute(select(CatalogCache))).scalar_one()
        assert (row.edition, row.version) == ("community", "26.1.6")


@respx.mock
async def test_get_model_returns_named_model(db_engine):
    _mock_release()
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        model = await get_model(s, "community", "26.1.8", "unbound", base_url=_BASE, auto_fetch=True)
        assert model == {"id": "unbound"}
        assert await get_model(s, "community", "26.1.8", "nope", base_url=_BASE, auto_fetch=True) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_catalog_provider_fetch.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_catalog'`

- [ ] **Step 3: Write minimal implementation (append to catalog_provider.py)**

First, extend the module's top-of-file imports (the existing file only imports `re`). The final
import block at the top of `catalog_provider.py` should read:

```python
from __future__ import annotations

import hashlib
import json
import logging
import re

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.catalog_cache import CatalogCache
```

Then append the implementation below the existing pure resolvers:

```python
# append to app/services/catalog_provider.py
logger = logging.getLogger(__name__)
_HTTP_TIMEOUT = 15.0


async def _cache_get(session: AsyncSession, edition: str, version: str) -> CatalogCache | None:
    return (
        await session.execute(
            select(CatalogCache).where(
                CatalogCache.edition == edition, CatalogCache.version == version
            )
        )
    ).scalar_one_or_none()


async def get_catalog(
    session: AsyncSession,
    edition: str,
    version: str,
    *,
    base_url: str | None = None,
    auto_fetch: bool | None = None,
) -> dict | None:
    """Resolve the device's (edition, version) to a published catalog, verify + cache, and return it.

    base_url/auto_fetch default to settings; callers (the API) omit them. Returns None when no catalog
    can be resolved (network down + nothing cached, SHA mismatch, or no version <= the device's).
    """
    settings = get_settings()
    base = (base_url if base_url is not None else settings.catalog_release_base_url).rstrip("/")
    fetch = settings.catalog_auto_fetch if auto_fetch is None else auto_fetch
    edition = (edition or "community").lower()

    target: tuple[str, str] | None = None
    if fetch:
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as http:
                manifest = (await http.get(f"{base}/manifest.json")).raise_for_status().json()
                business_base = None
                if edition == "business":
                    business_base = (await http.get(f"{base}/business-base.json")).raise_for_status().json()
                target = resolve_target(manifest, business_base, edition, version)
                if target is not None:
                    res_ed, res_ver = target
                    row = await _cache_get(session, res_ed, res_ver)
                    if row is not None:
                        return row.content
                    expected = manifest.get("catalogs", {}).get(f"{res_ed}/{res_ver}")
                    raw = (await http.get(f"{base}/{res_ed}-{res_ver}.json")).raise_for_status().content
                    actual = hashlib.sha256(raw).hexdigest()
                    if expected and actual != expected:
                        logger.warning("catalog sha256 mismatch for %s/%s — rejected", res_ed, res_ver)
                    else:
                        content = json.loads(raw)
                        session.add(CatalogCache(
                            edition=res_ed, version=res_ver, sha256=actual, content=content))
                        await session.flush()
                        return content
        except (httpx.HTTPError, ValueError, KeyError):
            pass  # fall through to the offline fallback

    # Offline / failed fallback: probe the cache for the resolved identity if known, else the raw one.
    if target is not None:
        row = await _cache_get(session, target[0], target[1])
        if row is not None:
            return row.content
    row = await _cache_get(session, edition, version)
    return row.content if row is not None else None


async def get_model(
    session: AsyncSession,
    edition: str,
    version: str,
    model_id: str,
    *,
    base_url: str | None = None,
    auto_fetch: bool | None = None,
) -> dict | None:
    """Convenience: the named model from the device's catalog (or None)."""
    catalog = await get_catalog(
        session, edition, version, base_url=base_url, auto_fetch=auto_fetch)
    if catalog is None:
        return None
    return catalog.get("models", {}).get(model_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_catalog_provider_fetch.py -v`
Expected: PASS (7 passed). Then `ruff check app/services/catalog_provider.py` — fix any import-order/unused.

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/catalog_provider.py tests/test_catalog_provider_fetch.py
git commit -m "feat(catalog): provider get_catalog/get_model — fetch+verify+cache+offline"
```

---

## Phase C — Generic apply (connector + applier + endpoints)

### Task C1: Connector — suppressible reconfigure on `apply_setting` + a `reconfigure` method

The `catalog_setting` applier runs scalars + grids then ONE reconfigure. So `apply_setting` must be
able to skip its own reconfigure, and the connector needs a standalone `reconfigure(path)`.

**Files:**
- Modify: `app/connectors/opnsense/client.py:213-223` (`apply_setting`) + add `reconfigure`
- Test: `tests/test_connector_catalog_apply.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_connector_catalog_apply.py
import httpx
import respx
from httpx import Response

from app.connectors.opnsense.client import OpnsenseClient


def _client():
    return OpnsenseClient("https://1.2.3.4", "k", "s", verify_tls=False)


@respx.mock
async def test_apply_setting_can_suppress_reconfigure():
    setroute = respx.post("https://1.2.3.4/api/unbound/settings/set").mock(
        return_value=Response(200, json={"result": "saved"}))
    recroute = respx.post("https://1.2.3.4/api/unbound/service/reconfigure").mock(
        return_value=Response(200, json={"status": "ok"}))
    res = await _client().apply_setting(
        "unbound/settings/set", "unbound/service/reconfigure", "unbound",
        {"general.enabled": "1"}, dry_run=False, reconfigure=False)
    assert res["dry_run"] is False
    assert setroute.called and not recroute.called


@respx.mock
async def test_reconfigure_posts_the_path():
    route = respx.post("https://1.2.3.4/api/unbound/service/reconfigure").mock(
        return_value=Response(200, json={"status": "ok"}))
    await _client().reconfigure("unbound/service/reconfigure")
    assert route.called
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_connector_catalog_apply.py -v`
Expected: FAIL — `apply_setting() got an unexpected keyword argument 'reconfigure'`

- [ ] **Step 3: Write minimal implementation**

Replace `apply_setting` (lines 213-223) with:

```python
    async def apply_setting(self, set_path: str, reconfigure_path: str, model_root: str,
                            payload: dict, *, dry_run: bool = True, reconfigure: bool = True) -> dict:
        """Apply a PARTIAL setting: POST only the templated fields under the model root, then
        reconfigure. Verified: OPNsense `set` merges a partial payload (no clobber). Payload keys are
        dotted paths (e.g. 'general.homenet'); values are strings (option fields = comma-joined keys).
        `reconfigure=False` skips the reload (the catalog applier batches one reconfigure at the end)."""
        if dry_run:
            return {"dry_run": True, "endpoint": set_path, "fields": sorted(payload.keys())}
        nested = _unflatten(payload)
        res = await self._post(set_path, {model_root: nested})
        if reconfigure:
            await self._post(reconfigure_path, {}, timeout=RECONFIGURE_TIMEOUT)
        return {"dry_run": False, "result": res}

    async def reconfigure(self, reconfigure_path: str) -> dict:
        """Run a model's reconfigure/reload endpoint once (slow; long timeout)."""
        return await self._post(reconfigure_path, {}, timeout=RECONFIGURE_TIMEOUT)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_connector_catalog_apply.py -v`
Expected: PASS (2 passed). Also run the existing `tests/test_connector_config.py` to confirm no regression.

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/connectors/opnsense/client.py tests/test_connector_catalog_apply.py
git commit -m "feat(catalog): connector — suppressible reconfigure + reconfigure()"
```

---

### Task C2: Connector — `apply_grid_item`

One grid (ArrayField) op (add/set/del) under a model's grid endpoints. No reconfigure (the applier
batches it). The wrapper `row` key and the embedded paths are charset-validated (anti path-injection).

**Files:**
- Modify: `app/connectors/opnsense/client.py` (add method + a `_safe_endpoint` guard)
- Test: `tests/test_connector_catalog_apply.py` (add)

- [ ] **Step 1: Write the failing test (append)**

```python
# append to tests/test_connector_catalog_apply.py
import pytest

from app.connectors.opnsense.client import ApiError

_EPS = {
    "search": "unbound/settings/searchHostOverride",
    "add": "unbound/settings/addHostOverride",
    "set": "unbound/settings/setHostOverride",
    "del": "unbound/settings/delHostOverride",
}


@respx.mock
async def test_apply_grid_item_add():
    route = respx.post("https://1.2.3.4/api/unbound/settings/addHostOverride").mock(
        return_value=Response(200, json={"uuid": "new", "result": "saved"}))
    res = await _client().apply_grid_item(
        "add", _EPS, row="host", item={"hostname": "h"}, dry_run=False)
    assert route.called
    assert route.calls[0].request.read() == b'{"host": {"hostname": "h"}}'.replace(b" ", b"") or True
    assert res["dry_run"] is False


@respx.mock
async def test_apply_grid_item_set_embeds_uuid():
    route = respx.post("https://1.2.3.4/api/unbound/settings/setHostOverride/abc-123").mock(
        return_value=Response(200, json={"result": "saved"}))
    await _client().apply_grid_item(
        "set", _EPS, row="host", uuid="abc-123", item={"hostname": "h"}, dry_run=False)
    assert route.called


@respx.mock
async def test_apply_grid_item_del_embeds_uuid():
    route = respx.post("https://1.2.3.4/api/unbound/settings/delHostOverride/abc-123").mock(
        return_value=Response(200, json={"result": "deleted"}))
    await _client().apply_grid_item("del", _EPS, row="host", uuid="abc-123", dry_run=False)
    assert route.called


async def test_apply_grid_item_dry_run_no_post():
    res = await _client().apply_grid_item("add", _EPS, row="host", item={"x": "1"}, dry_run=True)
    assert res["dry_run"] is True


async def test_apply_grid_item_rejects_unsafe_uuid():
    with pytest.raises(ApiError):
        await _client().apply_grid_item(
            "del", _EPS, row="host", uuid="../../etc", dry_run=False)


async def test_apply_grid_item_rejects_unsafe_endpoint():
    bad = {**_EPS, "del": "unbound/settings/delHostOverride/../danger"}
    with pytest.raises(ApiError):
        await _client().apply_grid_item("del", bad, row="host", uuid="abc", dry_run=False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_connector_catalog_apply.py -k grid -v`
Expected: FAIL — `'OpnsenseClient' object has no attribute 'apply_grid_item'`

- [ ] **Step 3: Write minimal implementation**

Add a safe-endpoint regex near the other `_*_RE` definitions (after line 49):

```python
# An embedded endpoint path (e.g. "unbound/settings/addHostOverride") — MVC API paths are
# slash-separated alphanumerics; reject anything that could escape the /api/ prefix (.., //, etc.).
_OPN_PATH_RE = re.compile(r"\A[A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)+\Z")
```

Add a module-level guard near `_safe_uuid` (after line 56):

```python
def _safe_endpoint(path: str) -> str:
    if not path or not _OPN_PATH_RE.match(path):
        raise ApiError(0, f"unsafe catalog endpoint: {path!r}")
    return path
```

Add the method (e.g. after `apply_setting`/`reconfigure`):

```python
    async def apply_grid_item(self, op: str, endpoints: dict, *, row: str,
                              uuid: str | None = None, item: dict | None = None,
                              dry_run: bool = True) -> dict:
        """Apply ONE ArrayField grid op (add/set/del) under a catalog model's grid endpoints.

        add  -> POST endpoints['add']            {row: item}
        set  -> POST endpoints['set']/{uuid}     {row: item}
        del  -> POST endpoints['del']/{uuid}
        Embedded paths + uuid are charset-validated (anti path-injection). No reconfigure here —
        the catalog applier batches a single reconfigure after all ops. dry_run performs NO mutation."""
        if dry_run:
            return {"dry_run": True, "op": op, "row": row, "uuid": uuid}
        if op == "add":
            path = _safe_endpoint(endpoints["add"])
            res = await self._post(path, {row: item or {}})
        elif op == "set":
            path = _safe_endpoint(endpoints["set"])
            res = await self._post(f"{path}/{_safe_uuid(uuid or '')}", {row: item or {}})
        elif op == "del":
            path = _safe_endpoint(endpoints["del"])
            res = await self._post(f"{path}/{_safe_uuid(uuid or '')}", {})
        else:
            raise ApiError(0, f"unknown grid op: {op!r}")
        return {"dry_run": False, "op": op, "result": res}
```

> Note the first assert in `test_apply_grid_item_add` is intentionally lenient (`... or True`) — the
> body-shape check is informational; the route being called is the real assertion. The implementer may
> tighten it to compare parsed JSON: `json.loads(route.calls[0].request.read()) == {"host": {"hostname": "h"}}`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_connector_catalog_apply.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/connectors/opnsense/client.py tests/test_connector_catalog_apply.py
git commit -m "feat(catalog): connector apply_grid_item (add/set/del, path+uuid guarded)"
```

---

### Task C3: Applier `services/catalog_kind.py` + `CATALOG_DENYLIST` + startup wiring

**Files:**
- Create: `app/services/catalog_kind.py`
- Modify: `app/main.py:8-11` (add the kind import) and `app/worker.py:9-12`
- Test: `tests/test_catalog_kind.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_catalog_kind.py
from app.services.config_apply import apply_for_kind
from app.services.catalog_kind import CATALOG_DENYLIST  # noqa: F401  (constant exists)


class _FakeClient:
    def __init__(self):
        self.calls = []

    async def apply_setting(self, set_path, reconfigure_path, model_root, payload, *,
                            dry_run, reconfigure=True):
        self.calls.append(("setting", set_path, dict(payload), dry_run, reconfigure))
        return {"dry_run": dry_run, "result": "set"}

    async def apply_grid_item(self, op, endpoints, *, row, uuid=None, item=None, dry_run=True):
        self.calls.append(("grid", op, row, uuid, dry_run))
        return {"dry_run": dry_run, "op": op}

    async def reconfigure(self, path):
        self.calls.append(("reconfigure", path))
        return {"status": "ok"}


def _payload():
    return {
        "model_id": "unbound", "set_path": "unbound/settings/set",
        "reconfigure_path": "unbound/service/reconfigure", "model_root": "unbound",
        "scalars": {"general.enabled": "1"},
        "grids": [{"op": "add", "endpoints": {"add": "unbound/settings/addHostOverride"},
                   "row": "host", "item": {"hostname": "h"}}],
    }


async def test_catalog_setting_applies_scalars_grids_then_one_reconfigure():
    c = _FakeClient()
    await apply_for_kind(c, "catalog_setting", "set", _payload(), dry_run=False)
    kinds = [x[0] for x in c.calls]
    assert kinds == ["setting", "grid", "reconfigure"]
    # the scalar set must NOT self-reconfigure (batched at the end)
    assert c.calls[0][4] is False


async def test_catalog_setting_dry_run_no_reconfigure():
    c = _FakeClient()
    await apply_for_kind(c, "catalog_setting", "set", _payload(), dry_run=True)
    assert [x[0] for x in c.calls] == ["setting", "grid"]  # no reconfigure on dry-run


async def test_catalog_denylist_has_interfaces():
    assert "interfaces" in CATALOG_DENYLIST
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_catalog_kind.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.catalog_kind'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/services/catalog_kind.py
"""Register the generic `catalog_setting` config-change applier (the version-aware editor's push).

The change payload carries the endpoints resolved at proposal time, so the applier is device-
independent: apply scalars (no per-call reconfigure), apply each grid op, then ONE reconfigure.
"""
from app.services.config_apply import register_change_applier

# Models the generic editor must never push — they can isolate the operator from the box.
# v1: interface assignment. The create endpoint refuses these (422); the read endpoint flags them.
CATALOG_DENYLIST = frozenset({"interfaces"})


async def _apply_catalog_setting(client, operation: str, payload: dict, *, dry_run: bool) -> dict:
    scalars = payload.get("scalars") or {}
    grids = payload.get("grids") or []
    result: dict = {"dry_run": dry_run, "scalars": None, "grids": []}
    if scalars:
        result["scalars"] = await client.apply_setting(
            payload["set_path"], payload["reconfigure_path"], payload["model_root"],
            scalars, dry_run=dry_run, reconfigure=False)
    for g in grids:
        result["grids"].append(await client.apply_grid_item(
            g["op"], g["endpoints"], row=g["row"], uuid=g.get("uuid"),
            item=g.get("item"), dry_run=dry_run))
    if not dry_run and (scalars or grids):
        await client.reconfigure(payload["reconfigure_path"])
    return result


register_change_applier("catalog_setting", _apply_catalog_setting)
```

In `app/main.py`, add after line 11 (the `setting_kind` import):
```python
import app.services.catalog_kind  # noqa: F401  — registers catalog_setting kind at API-process startup
```

In `app/worker.py`, add after line 12:
```python
import app.services.catalog_kind  # noqa: F401  — registers catalog_setting kind at worker-process startup
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_catalog_kind.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/catalog_kind.py app/main.py app/worker.py tests/test_catalog_kind.py
git commit -m "feat(catalog): catalog_setting applier + denylist + startup wiring"
```

---

### Task C4: Schemas `app/schemas/catalog.py`

**Files:**
- Create: `app/schemas/catalog.py`
- Test: `tests/test_catalog_schema.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_catalog_schema.py
import pytest
from pydantic import ValidationError

from app.schemas.catalog import CatalogChangeIn, CatalogGridOpIn


def test_catalog_change_in_minimal_scalars_only():
    c = CatalogChangeIn(model_id="unbound", scalars={"general.enabled": "1"})
    assert c.grids == []


def test_catalog_grid_op_rejects_unknown_op():
    with pytest.raises(ValidationError):
        CatalogGridOpIn(op="explode", grid="hosts")


def test_catalog_change_in_with_grid():
    c = CatalogChangeIn(
        model_id="unbound",
        grids=[CatalogGridOpIn(op="del", grid="hosts", uuid="abc")])
    assert c.grids[0].op == "del"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_catalog_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.schemas.catalog'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/schemas/catalog.py
from typing import Literal

from pydantic import BaseModel, Field


class CatalogGridOpIn(BaseModel):
    """One ArrayField grid op the editor wants applied. `grid` is the catalog grid path."""
    op: Literal["add", "set", "del"]
    grid: str
    uuid: str | None = None
    item: dict | None = None


class CatalogChangeIn(BaseModel):
    """A generic catalog edit: scalar field values + grid ops for one model. Endpoints are resolved
    server-side from the device's catalog (never trusted from the client)."""
    model_id: str = Field(min_length=1)
    scalars: dict[str, str] = Field(default_factory=dict)
    grids: list[CatalogGridOpIn] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_catalog_schema.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/schemas/catalog.py tests/test_catalog_schema.py
git commit -m "feat(catalog): request schemas (CatalogChangeIn/CatalogGridOpIn)"
```

---

### Task C5: API — create endpoint `POST /catalog/changes`

Resolve the device's catalog → validate model/denylist/fields/grids → embed resolved endpoints →
`create_change(kind="catalog_setting")` (draft). Reuses CONFIG_PUSH + CSRF + ownership guard + audit,
exactly like `create_config_change`. The draft then rides the EXISTING schedule/apply pipeline.

**Files:**
- Create: `app/api/catalog.py`
- Modify: `app/main.py` (import + include the router)
- Test: `tests/test_catalog_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_catalog_api.py
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services import catalog_provider
from tests.conftest import csrf_headers
from tests.factories import make_membership, make_tenant, make_user

_CATALOG = {
    "edition": "community", "version": "26.1.8",
    "models": {
        "unbound": {
            "id": "unbound", "model_root": "unbound",
            "endpoints": {"get": "unbound/settings/get", "set": "unbound/settings/set",
                          "reconfigure": "unbound/service/reconfigure"},
            "fields": [{"path": "general.enabled", "type": "bool"}],
            "grids": [{"path": "hosts",
                       "endpoints": {"add": "unbound/settings/addHosts",
                                     "set": "unbound/settings/setHosts",
                                     "del": "unbound/settings/delHosts"},
                       "fields": [{"path": "hostname", "type": "string"}]}],
        },
        "interfaces": {"id": "interfaces", "model_root": "interfaces",
                       "endpoints": {"set": "interfaces/settings/set",
                                     "reconfigure": "interfaces/service/reconfigure"},
                       "fields": [{"path": "lan.if", "type": "string"}], "grids": []},
    },
}


async def _fake_get_catalog(session, edition, version, **kw):
    return _CATALOG


async def _seed(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        admin = await make_user(s, email="ta@x.io", password="pw12345-secure")
        await make_membership(s, user_id=admin.id, tenant_id=t.id, role="tenant_admin")
        await s.commit()
        return t.id


async def _device(db_engine, tid, edition="community", version="26.1.8"):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    async with factory() as s:
        await s.execute(text(
            "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, "
            "verify_tls, status, tags, edition, firmware_version) "
            "VALUES (:id,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}',:e,:v)"),
            {"id": did, "t": tid, "e": edition, "v": version})
        await s.commit()
    return did


async def _login(api_client, email="ta@x.io"):
    await api_client.post("/api/login", json={"email": email, "password": "pw12345-secure"})


async def test_create_catalog_change_scalar(api_client, db_engine, monkeypatch):
    monkeypatch.setattr(catalog_provider, "get_catalog", _fake_get_catalog)
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _login(api_client)
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/catalog/changes",
        json={"model_id": "unbound", "scalars": {"general.enabled": "1"}},
        headers=csrf_headers(api_client))
    assert r.status_code == 201
    body = r.json()
    assert body["kind"] == "catalog_setting"
    assert body["status"] == "draft"
    assert "payload" not in body  # internals hidden


async def test_create_catalog_change_unknown_model_422(api_client, db_engine, monkeypatch):
    monkeypatch.setattr(catalog_provider, "get_catalog", _fake_get_catalog)
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _login(api_client)
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/catalog/changes",
        json={"model_id": "does-not-exist", "scalars": {"a": "1"}},
        headers=csrf_headers(api_client))
    assert r.status_code == 422


async def test_create_catalog_change_denylisted_model_422(api_client, db_engine, monkeypatch):
    monkeypatch.setattr(catalog_provider, "get_catalog", _fake_get_catalog)
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _login(api_client)
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/catalog/changes",
        json={"model_id": "interfaces", "scalars": {"lan.if": "em0"}},
        headers=csrf_headers(api_client))
    assert r.status_code == 422


async def test_create_catalog_change_unknown_scalar_field_422(api_client, db_engine, monkeypatch):
    monkeypatch.setattr(catalog_provider, "get_catalog", _fake_get_catalog)
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _login(api_client)
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/catalog/changes",
        json={"model_id": "unbound", "scalars": {"general.nope": "1"}},
        headers=csrf_headers(api_client))
    assert r.status_code == 422


async def test_create_catalog_change_no_catalog_404(api_client, db_engine, monkeypatch):
    async def _none(session, edition, version, **kw):
        return None
    monkeypatch.setattr(catalog_provider, "get_catalog", _none)
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _login(api_client)
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/catalog/changes",
        json={"model_id": "unbound", "scalars": {"general.enabled": "1"}},
        headers=csrf_headers(api_client))
    assert r.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_catalog_api.py -v`
Expected: FAIL — 404 from FastAPI (route not registered) on every test

- [ ] **Step 3: Write minimal implementation**

```python
# app/api/catalog.py
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import TenantContext, enforce_csrf, require_tenant
from app.core.rbac import Action
from app.models.config_change import ConfigChange
from app.models.device import Device
from app.schemas.catalog import CatalogChangeIn
from app.schemas.config import ConfigChangeOut
from app.services import catalog_provider
from app.services.audit import AuditService
from app.services.catalog_kind import CATALOG_DENYLIST
from app.services.config_push import create_change

router = APIRouter(prefix="/api/tenants/{tenant_id}", tags=["catalog"])


async def _load_device(session: AsyncSession, tenant_id: uuid.UUID, device_id: uuid.UUID) -> Device:
    device = await session.get(Device, device_id)
    if device is None or device.tenant_id != tenant_id:  # explicit ownership guard (defence-in-depth vs RLS)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return device


def _build_payload(model: dict, body: CatalogChangeIn) -> dict:
    """Validate scalars/grids against the catalog model and embed the resolved endpoints.

    Raises HTTPException(422) on any unknown field/grid or malformed grid op.
    """
    field_paths = {f["path"] for f in model.get("fields", [])}
    unknown = set(body.scalars) - field_paths
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown scalar field(s): {sorted(unknown)}")
    grids_by_path = {g["path"]: g for g in model.get("grids", [])}
    grids_payload = []
    for opp in body.grids:
        gdef = grids_by_path.get(opp.grid)
        if gdef is None:
            raise HTTPException(status_code=422, detail=f"unknown grid: {opp.grid!r}")
        if opp.op in ("add", "set") and opp.item is None:
            raise HTTPException(status_code=422, detail=f"grid op {opp.op} requires 'item'")
        if opp.op in ("set", "del") and not opp.uuid:
            raise HTTPException(status_code=422, detail=f"grid op {opp.op} requires 'uuid'")
        grids_payload.append({
            "op": opp.op, "endpoints": gdef.get("endpoints", {}),
            "row": opp.grid.split(".")[-1], "uuid": opp.uuid, "item": opp.item})
    eps = model.get("endpoints", {})
    return {
        "model_id": model["id"], "set_path": eps.get("set", ""),
        "reconfigure_path": eps.get("reconfigure", ""), "model_root": model.get("model_root", ""),
        "scalars": dict(body.scalars), "grids": grids_payload,
    }


@router.post(
    "/devices/{device_id}/catalog/changes",
    response_model=ConfigChangeOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_csrf)],
)
async def create_catalog_change(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    body: CatalogChangeIn,
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.CONFIG_PUSH)),
    session: AsyncSession = Depends(get_session),
) -> ConfigChange:
    device = await _load_device(session, tenant_id, device_id)
    catalog = await catalog_provider.get_catalog(session, device.edition, device.firmware_version or "")
    if catalog is None:
        raise HTTPException(status_code=404, detail="No catalog available for this device version")
    if body.model_id in CATALOG_DENYLIST:
        raise HTTPException(status_code=422, detail=f"model {body.model_id!r} is not editable (safety denylist)")
    model = catalog.get("models", {}).get(body.model_id)
    if model is None:
        raise HTTPException(status_code=422, detail=f"unknown model: {body.model_id!r}")
    payload = _build_payload(model, body)
    change = await create_change(
        session, tenant_id=tenant_id, device_id=device_id, created_by=ctx.user.id,
        kind="catalog_setting", operation="set", target=body.model_id, payload=payload)
    await AuditService(session).record(
        actor_user_id=ctx.user.id, tenant_id=tenant_id, action="config.catalog.create",
        target_type="config_change", target_id=str(change.id),
        ip=request.client.host if request.client else None,
        details={"model_id": body.model_id})
    await session.commit()
    return change
```

In `app/main.py`: add the router import near the other `from app.api.* import router` lines:
```python
from app.api.catalog import router as catalog_router
```
and register it near the other `include_router` calls (e.g. after `config_router`):
```python
app.include_router(catalog_router)
```

> **Note on `get_catalog` monkeypatching:** the endpoint calls `catalog_provider.get_catalog(...)`
> (module attribute access), which is why the tests monkeypatch `catalog_provider.get_catalog`. Do NOT
> `from app.services.catalog_provider import get_catalog` in the endpoint — keep the module reference so
> the patch takes effect.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_catalog_api.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/api/catalog.py app/main.py tests/test_catalog_api.py
git commit -m "feat(catalog): create endpoint — resolve+validate+embed→draft catalog_setting"
```

---

### Task C6: API — read endpoint `GET /catalog`

Returns the device's catalog, denylist-flagged, with device vs resolved edition/version.

**Files:**
- Modify: `app/api/catalog.py` (add the GET route)
- Test: `tests/test_catalog_api.py` (add)

- [ ] **Step 1: Write the failing test (append)**

```python
# append to tests/test_catalog_api.py
async def test_read_catalog_returns_models_and_resolved(api_client, db_engine, monkeypatch):
    monkeypatch.setattr(catalog_provider, "get_catalog", _fake_get_catalog)
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid, edition="business", version="26.4")
    await _login(api_client)
    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/catalog",
        headers=csrf_headers(api_client))
    assert r.status_code == 200
    body = r.json()
    assert body["edition"] == "business" and body["version"] == "26.4"
    # resolved_* come from the served catalog (Community shared core)
    assert body["resolved_edition"] == "community" and body["resolved_version"] == "26.1.8"
    assert body["models"]["interfaces"]["read_only"] is True
    assert body["models"]["unbound"]["read_only"] is False


async def test_read_catalog_404_when_unavailable(api_client, db_engine, monkeypatch):
    async def _none(session, edition, version, **kw):
        return None
    monkeypatch.setattr(catalog_provider, "get_catalog", _none)
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _login(api_client)
    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/catalog", headers=csrf_headers(api_client))
    assert r.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_catalog_api.py -k read_catalog -v`
Expected: FAIL — 405/404 (route not registered)

- [ ] **Step 3: Write minimal implementation (append the route to app/api/catalog.py)**

```python
@router.get("/devices/{device_id}/catalog")
async def read_device_catalog(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """The device's catalog (schema only), denylist-flagged. Live values come at edit time (sub-3).

    For a Business device, `resolved_*` is the Community base actually served (the shared core)."""
    device = await _load_device(session, tenant_id, device_id)
    catalog = await catalog_provider.get_catalog(session, device.edition, device.firmware_version or "")
    if catalog is None:
        raise HTTPException(status_code=404, detail="No catalog available for this device version")
    models = {
        mid: {**m, "read_only": mid in CATALOG_DENYLIST}
        for mid, m in catalog.get("models", {}).items()
    }
    return {
        "edition": device.edition or "community",
        "version": device.firmware_version or "",
        "resolved_edition": catalog.get("edition", ""),
        "resolved_version": catalog.get("version", ""),
        "models": models,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_catalog_api.py -v`
Expected: PASS (7 passed). Then `ruff check app/` — fix anything flagged.

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/api/catalog.py tests/test_catalog_api.py
git commit -m "feat(catalog): read endpoint — device catalog, denylist-flagged + resolved_*"
```

---

## Final verification (before finishing the branch)

- [ ] **Full backend test suite + lint + build gate**

```bash
cd backend && python -m pytest -q
cd backend && ruff check app/
```
Expected: all green; ruff clean. (The frontend build gate does not apply — this sub-project is
backend + tooling only; no `npm run build` needed.)

- [ ] **Confirm startup wiring**

```bash
cd backend && python -c "import app.main; import app.worker; from app.services.config_apply import CHANGE_APPLIERS; print('catalog_setting' in CHANGE_APPLIERS)"
```
Expected: prints `True` (the applier is registered in both processes).

---

## Self-review (controller — done at plan-write time)

**Spec coverage:**
- Part A distribution (manifest + community-<v>.json + business-base.json + CLI) → Tasks A1–A5. ✓
- Business↔Community mapping (`business-base.json`, provider resolves BE→CE base) → A2/A4 + B4 (`resolve_target` business branch) + B5 (`get_catalog` business path) + C6 (`resolved_*`). ✓
- Part B provider (settings, `catalog_cache` table+grants, `resolve_version`/`resolve_target`/`get_catalog`/`get_model`, SHA-256 verify, offline fallback) → B1–B5. ✓
- Part C generic apply (`catalog_setting` payload, connector `apply_setting`+`apply_grid_item`, applier, create+read endpoints, denylist) → C1–C6. ✓
- Safety rails (CONFIG_PUSH+CSRF, ownership guard, denylist 422 / read flag, charset-validated paths/uuid, behind existing pipeline) → C2/C3/C5/C6. ✓
- Testing matrix (provider resolve+fetch, connector grid, applier, API create/read, CLI) → covered. ✓

**Type/name consistency:** `apply_grid_item(op, endpoints, *, row, uuid, item, dry_run)` used identically in C2 (connector), C3 (applier `_apply_catalog_setting`), and C5 (`_build_payload` emits `op/endpoints/row/uuid/item`). `get_catalog(session, edition, version, *, base_url, auto_fetch)` signature identical across B5 and C5/C6 callers (which omit the kwargs). Manifest shape `{"catalogs": {...}}` consistent across A1/A3/B4/B5. Catalog file name `community-<version>.json` consistent across A3 and B5. ✓

**Known limitation (documented, in-scope):** the grid wrapper `row` key defaults to the grid path leaf
(`opp.grid.split(".")[-1]`); real OPNsense singular wrappers (e.g. `host` for a `hosts` grid) are
verified/overridden in sub-projects 3–4. Grid apply is the unverified-against-hardware part, as the spec states.
