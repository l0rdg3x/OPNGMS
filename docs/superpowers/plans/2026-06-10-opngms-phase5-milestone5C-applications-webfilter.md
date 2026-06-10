# OPNGMS — Phase 5 / Milestone 5C: Applications + Web Filter (mock) + Threat-Level — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the reference layout's **Applications** and **Web Filter** sections to the report as clearly-labeled **deterministic mock data**, with **threat-level color coding** (Low=green, Guarded=blue, High=orange) — completing the full report template look without implying the numbers are real.

**Architecture:** A new pure, deterministic `mock_sections.py` provider (seeded by device name, no DB, no `random`) builds Applications/Web Filter blocks; new context dataclasses carry threat-tagged rows; the template renders the two sections (each with a visible "sample data" caption + threat badges). When real app-id/category ingest lands later, the provider is swapped for a real aggregator with the same block shape — no template change.

**Tech Stack:** Python 3.12+, dataclasses, hashlib (deterministic seed), Jinja2 (autoescape), hand-built SVG; pytest.

---

## Context for the implementer (read first)

Codebase is **English**. Backend only. 5A + 5B in `main`.

**Current `context.py`** has `human_bytes`, dataclasses `RankedTable(title, columns:tuple[str,str], rows:list[tuple[str,int]])`, `AttacksBlock`, `WebActivityBlock`, `BandwidthBlock`, `StatusBlock`, `DeviceSection(device_name, attacks, web, bandwidth, status)`, `ReportContext`, and `async build_context(...)` that loops devices and builds attacks/web/bandwidth/status per device. At the **bottom** of the file (after the dataclasses) it imports `aggregation`/`charts` (`# noqa: E402`) — follow that pattern for the new `mock_sections` import to avoid a circular import.

**`charts.line_chart(points, *, width, height)`** returns an SVG string (pure, numeric — labels discarded). **Template** `templates/report.html.j2` renders per-device `<section>`s with independent `{% if %}` guards per block; ranked tables use a `<caption>` + 2-column `<thead>` pattern. **`template.py`** has autoescape ON; only `Markup(...)`-wrapped SVG strings are safe.

**Commands** (from `backend/`):
```bash
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test \
ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test \
.venv/bin/python -m pytest -q
```
The mock-provider tests are pure (no DB); the render test uses `build_context` (needs the DB for `devices()`).

**Security:** autoescape stays ON. Mock strings are trusted constants but still render as escaped text. Threat level is a **controlled enum** (`low|guarded|high`) → safe CSS class. No DB, no secrets, no SSRF surface.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `app/services/reporting/context.py` | new threat dataclasses + Applications/WebFilter blocks + wire into `build_context` | Modify |
| `app/services/reporting/mock_sections.py` | deterministic per-device mock providers | Create |
| `app/services/reporting/templates/report.html.j2` | render Applications + Web Filter sections | Modify |
| `app/services/reporting/templates/report.css` | threat badge + sample-note styles | Modify |
| `tests/test_report_mock_sections.py` | provider tests | Create |
| `tests/test_report_context.py` | render assertions | Modify |

---

## Task 1: Threat model + deterministic mock provider

**Files:** Modify `context.py` (dataclasses only); Create `mock_sections.py`, `tests/test_report_mock_sections.py`.

- [ ] **Step 1: Add dataclasses to `context.py`** (next to the other dataclasses, BEFORE the bottom imports):
```python
@dataclass
class ThreatRow:
    label: str
    count: int
    level: str   # controlled enum: "low" | "guarded" | "high"


@dataclass
class ThreatRankedTable:
    title: str
    columns: tuple[str, str]          # (label header, count header); a "Threat" column is implicit
    rows: list["ThreatRow"]


@dataclass
class ApplicationsBlock:
    timeline_svg: str
    top_detected: "ThreatRankedTable"
    top_blocked: "ThreatRankedTable"
    top_categories: "ThreatRankedTable"
    top_initiators: RankedTable
    sample: bool = True


@dataclass
class WebFilterBlock:
    timeline_svg: str
    top_categories: "ThreatRankedTable"
    top_sites: RankedTable
    top_initiators: RankedTable
    sample: bool = True
```
And extend `DeviceSection`:
```python
@dataclass
class DeviceSection:
    device_name: str
    attacks: AttacksBlock | None = None
    web: "WebActivityBlock | None" = None
    bandwidth: "BandwidthBlock | None" = None
    status: "StatusBlock | None" = None
    applications: "ApplicationsBlock | None" = None
    web_filter: "WebFilterBlock | None" = None
```

- [ ] **Step 2: Write the failing provider test** — create `tests/test_report_mock_sections.py`:
```python
from app.services.reporting.mock_sections import applications_block, web_filter_block

LEVELS = {"low", "guarded", "high"}


def test_applications_block_deterministic_and_per_device():
    a1 = applications_block("fw-edge")
    a2 = applications_block("fw-edge")
    other = applications_block("fw-branch")
    assert a1 == a2                       # deterministic for the same device name
    assert a1 != other                    # per-device distinct
    assert a1.sample is True
    assert a1.timeline_svg.startswith("<svg")
    for tbl in (a1.top_detected, a1.top_blocked, a1.top_categories):
        assert tbl.rows
        assert all(r.level in LEVELS for r in tbl.rows)
        assert all(r.count >= 1 for r in tbl.rows)
    assert a1.top_initiators.rows


def test_web_filter_block_deterministic_and_levels():
    w1 = web_filter_block("fw-edge")
    w2 = web_filter_block("fw-edge")
    assert w1 == w2
    assert w1.sample is True
    assert all(r.level in LEVELS for r in w1.top_categories.rows)
    assert w1.top_sites.rows and w1.top_initiators.rows
```
Run → FAIL (`mock_sections` missing).

- [ ] **Step 3: Implement `mock_sections.py`** — create `app/services/reporting/mock_sections.py`:
```python
"""Deterministic, per-device MOCK providers for the Applications and Web Filter report sections.

No real app-id/flow/content-categorization is ingested yet; these blocks are clearly labeled as sample
data in the template. Output is deterministic (seeded by device name) so it is stable and testable, and
distinct per device. When a real feed lands, swap these for a real aggregator with the same block shape.
"""
from __future__ import annotations

import hashlib

from app.services.reporting.charts import line_chart
from app.services.reporting.context import (
    ApplicationsBlock,
    RankedTable,
    ThreatRankedTable,
    ThreatRow,
    WebFilterBlock,
)

# Fixed palettes with a fixed threat level each (controlled enum values only).
_APPS = [
    ("Microsoft 365", "low"), ("Zoom", "low"), ("WhatsApp", "low"),
    ("Dropbox", "guarded"), ("TikTok", "guarded"), ("Steam", "guarded"),
    ("BitTorrent", "high"), ("Tor", "high"), ("TeamViewer", "high"),
]
_CATEGORIES = [
    ("Business", "low"), ("Streaming Media", "guarded"), ("Social Networking", "guarded"),
    ("File Sharing", "high"), ("Gaming", "guarded"), ("Advertising", "guarded"),
    ("Malware", "high"), ("News", "low"),
]
_SITES = ["cdn.jsdelivr.net", "news.example.com", "ads.doubleclick.net", "drive.google.com", "facebook.com", "github.com"]
_INITIATORS = ["10.0.0.10", "10.0.0.21", "10.0.0.42", "10.0.0.55", "10.0.0.73"]


def _seed(name: str) -> int:
    # Stable across processes (unlike hash()); PYTHONHASHSEED-independent.
    return int.from_bytes(hashlib.sha1(name.encode("utf-8")).digest()[:4], "big")


def _rotate(items: list, seed: int) -> list:
    if not items:
        return items
    k = seed % len(items)
    return items[k:] + items[:k]


def _counts(seed: int, n: int) -> list[int]:
    base = 40 + (seed % 160)               # per-device magnitude
    return [max(1, base // (i + 1) + (seed >> (i % 5)) % 7) for i in range(n)]


def _timeline_svg(seed: int, *, height: int = 140) -> str:
    pts = [(f"t{i}", 5 + (seed >> (i % 6)) % 40 + (i % 3) * 3) for i in range(6)]
    return line_chart(pts, width=520, height=height)


def _threat_table(title: str, columns: tuple[str, str], palette: list[tuple[str, str]], seed: int, n: int) -> ThreatRankedTable:
    rotated = _rotate(palette, seed)[:n]
    counts = _counts(seed, len(rotated))
    rows = [ThreatRow(label=label, count=c, level=level) for (label, level), c in zip(rotated, counts)]
    return ThreatRankedTable(title=title, columns=columns, rows=rows)


def _plain_table(title: str, columns: tuple[str, str], items: list[str], seed: int, n: int) -> RankedTable:
    rotated = _rotate(items, seed)[:n]
    counts = _counts(seed, len(rotated))
    return RankedTable(title=title, columns=columns, rows=list(zip(rotated, counts)))


def applications_block(device_name: str) -> ApplicationsBlock:
    seed = _seed(device_name)
    return ApplicationsBlock(
        timeline_svg=_timeline_svg(seed),
        top_detected=_threat_table("Top Detected", ("Application", "Sessions"), _APPS, seed, 5),
        top_blocked=_threat_table("Top Blocked", ("Application", "Blocks"), _rotate(_APPS, seed + 3), seed + 3, 4),
        top_categories=_threat_table("Top Categories", ("Category", "Sessions"), _CATEGORIES, seed, 5),
        top_initiators=_plain_table("Top Initiators", ("Initiator", "Sessions"), _INITIATORS, seed, 4),
    )


def web_filter_block(device_name: str) -> WebFilterBlock:
    seed = _seed(device_name) ^ 0x5F5F
    return WebFilterBlock(
        timeline_svg=_timeline_svg(seed, height=140),
        top_categories=_threat_table("Top Categories", ("Category", "Requests"), _CATEGORIES, seed, 5),
        top_sites=_plain_table("Top Sites", ("Site", "Requests"), _SITES, seed, 5),
        top_initiators=_plain_table("Top Initiators", ("Initiator", "Requests"), _INITIATORS, seed, 4),
    )
```
Run Step 2 → PASS.

- [ ] **Step 4: Commit**
```bash
git add app/services/reporting/context.py app/services/reporting/mock_sections.py tests/test_report_mock_sections.py
git commit -m "feat(reporting): deterministic mock Applications/Web Filter providers + threat model"
```

---

## Task 2: Wire into context + render the sections (threat colors + sample label)

**Files:** Modify `context.py` (`build_context`), `templates/report.html.j2`, `templates/report.css`, `tests/test_report_context.py`.

- [ ] **Step 1: Write the failing render test** — append to `tests/test_report_context.py`:
```python
async def test_build_context_includes_applications_and_web_filter(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tid = t.id
    from sqlalchemy import text
    did = uuid.uuid4()
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:
        await s.execute(text("INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                             "VALUES (:id,:t,'fw-edge','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"id": did, "t": tid})
        await s.commit()
    async with factory() as s:
        agg = ReportAggregator(s, tid)
        ctx = await build_context(agg, tenant_name="Acme", timezone_name="UTC", owner=None,
                                  frm=base - timedelta(hours=1), to=base + timedelta(hours=1))
    sec = ctx.sections[0]
    assert sec.applications is not None and sec.web_filter is not None
    html = render_html(ctx)
    assert "Applications" in html and "Web Filter" in html
    assert "Sample data" in html                 # honesty caption
    assert "threat-high" in html or "threat-low" in html or "threat-guarded" in html
```
Run → FAIL.

- [ ] **Step 2: Wire into `build_context`** — add the bottom import alongside the others:
```python
from app.services.reporting.mock_sections import applications_block, web_filter_block  # noqa: E402
```
In the device loop, before the `sections.append(...)`, add:
```python
        # --- Applications + Web Filter (deterministic MOCK; labeled as sample data in the template) ---
        applications = applications_block(dev.name)
        web_filter = web_filter_block(dev.name)
```
and change the append to include them:
```python
        sections.append(DeviceSection(
            device_name=dev.name, attacks=attacks, web=web, bandwidth=bandwidth, status=status,
            applications=applications, web_filter=web_filter,
        ))
```

- [ ] **Step 3: Template** — in `report.html.j2`, inside the device `<section>`, AFTER the `{% if section.status %}...{% endif %}` block and before `</section>`, add:
```jinja
    {% if section.applications %}
    <h3>Applications</h3>
    <p class="sample-note">Sample data — application visibility not yet ingested.</p>
    <div class="chart">{{ Markup(section.applications.timeline_svg) }}</div>
    {% for tbl in [section.applications.top_detected, section.applications.top_blocked, section.applications.top_categories] %}
    <table class="ranked">
      <caption>{{ tbl.title }}</caption>
      <thead><tr><th>{{ tbl.columns[0] }}</th><th>{{ tbl.columns[1] }}</th><th>Threat</th></tr></thead>
      <tbody>
        {% for row in tbl.rows %}
        <tr><td>{{ row.label }}</td><td>{{ row.count }}</td>
            <td><span class="threat threat-{{ row.level }}">{{ row.level }}</span></td></tr>
        {% endfor %}
      </tbody>
    </table>
    {% endfor %}
    <table class="ranked">
      <caption>{{ section.applications.top_initiators.title }}</caption>
      <thead><tr><th>{{ section.applications.top_initiators.columns[0] }}</th><th>{{ section.applications.top_initiators.columns[1] }}</th></tr></thead>
      <tbody>
        {% for value, count in section.applications.top_initiators.rows %}<tr><td>{{ value }}</td><td>{{ count }}</td></tr>{% endfor %}
      </tbody>
    </table>
    {% endif %}

    {% if section.web_filter %}
    <h3>Web Filter</h3>
    <p class="sample-note">Sample data — content categorization not yet ingested.</p>
    <div class="chart">{{ Markup(section.web_filter.timeline_svg) }}</div>
    <table class="ranked">
      <caption>{{ section.web_filter.top_categories.title }}</caption>
      <thead><tr><th>{{ section.web_filter.top_categories.columns[0] }}</th><th>{{ section.web_filter.top_categories.columns[1] }}</th><th>Threat</th></tr></thead>
      <tbody>
        {% for row in section.web_filter.top_categories.rows %}
        <tr><td>{{ row.label }}</td><td>{{ row.count }}</td>
            <td><span class="threat threat-{{ row.level }}">{{ row.level }}</span></td></tr>
        {% endfor %}
      </tbody>
    </table>
    {% for tbl in [section.web_filter.top_sites, section.web_filter.top_initiators] %}
    <table class="ranked">
      <caption>{{ tbl.title }}</caption>
      <thead><tr><th>{{ tbl.columns[0] }}</th><th>{{ tbl.columns[1] }}</th></tr></thead>
      <tbody>
        {% for value, count in tbl.rows %}<tr><td>{{ value }}</td><td>{{ count }}</td></tr>{% endfor %}
      </tbody>
    </table>
    {% endfor %}
    {% endif %}
```
(`row.level` is the controlled enum `low|guarded|high`; it is autoescaped and only ever those values.)

- [ ] **Step 4: CSS** — add to `report.css`:
```css
.threat { display: inline-block; padding: 1px 6px; border-radius: 3px; color: #fff; font-size: 8pt; }
.threat-low { background: #2f9e44; }
.threat-guarded { background: #1971c2; }
.threat-high { background: #e8590c; }
.sample-note { font-size: 8pt; color: #b08900; font-style: italic; margin: 2px 0 6px; }
```

- [ ] **Step 5: Run + commit** — Step 1 test PASS; full suite green; (optional) render a PDF to eyeball.
```bash
git add app/services/reporting/context.py app/services/reporting/templates/report.html.j2 \
        app/services/reporting/templates/report.css tests/test_report_context.py
git commit -m "feat(reporting): render Applications + Web Filter (mock) with threat-level coloring"
```

---

## Task 3: Technical debt

- [ ] **Step 1: Append**
```markdown
## Technical debt (5C)

- **Applications/Web Filter are MOCK** (clearly labeled): real app-id/flow + content categorization needs
  an OPNsense visibility plugin + a category feed (future ingest phase). The block shape is real, so a
  later real aggregator drops in without template changes.
- **Threat levels are fixed per palette entry** (mock); a real feed would assign them from policy/IP-rep.
```

- [ ] **Step 2: Commit**
```bash
git add docs/superpowers/plans/2026-06-10-opngms-phase5-milestone5C-applications-webfilter.md
git commit -m "docs: technical debt milestone 5C"
```

---

## Technical debt (5C) — recorded

- **Applications/Web Filter are MOCK** (clearly labeled as sample data): real app-id/flow + content
  categorization needs an OPNsense visibility plugin + a category feed (future ingest phase). The block
  shape is real, so a later real aggregator drops in without template changes.
- **Threat levels are fixed per palette entry** (mock); a real feed would assign them from policy/IP-rep.
- **Circular import** between `context` and `mock_sections` resolved via a local import inside
  `build_context`.

---

## Definition of "Done" (5C)
- A generated report shows, per firewall, **Applications** (timeline + Top Detected/Blocked/Categories
  with Low/Guarded/High threat badges + Top Initiators) and **Web Filter** (Categories/Sites/Initiators +
  timeline), each clearly labeled **sample data**.
- Deterministic + per-device-distinct; autoescaped; threat colors via controlled CSS classes; PDF valid;
  backend suite green; no migration.
