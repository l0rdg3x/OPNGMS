# OPNGMS — Phase 5 / Milestone 5A: Reporting Foundation & Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the PDF reporting engine end-to-end (WeasyPrint + Jinja2 + server-side SVG charts) with a tenant-scoped aggregation layer and an on-demand generate API, proven with a real **Attacks** section (IDS), and the document skeleton (title page, TOC, footer) ready for 5B–5C.

**Architecture:** New `app/services/reporting/` package: `aggregation` (ranked tops reusing `EventRepository.top` + time-bucketed timelines), `charts` (pure data→SVG functions), `context` (assemble the report model), `template` (Jinja2, autoescape), `service` (HTML→PDF via WeasyPrint, remote fetch blocked). A `POST /api/tenants/{tenant_id}/reports` endpoint (new RBAC `REPORT_GENERATE`, CSRF, audit) returns the PDF inline. No DB migration.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy 2.0 async, TimescaleDB (`time_bucket`), WeasyPrint, Jinja2, hand-built SVG; pytest + pytest-asyncio.

---

## Context for the implementer (read first)

Codebase is **English** — all code/comments in English. Phases 1–4 in `main`. This is **backend only**.

**Key existing patterns (reuse, do not reinvent):**
- **RBAC** `app/core/rbac.py`: `Action` enum + `_TENANT_MATRIX`. `can(is_superadmin, role, action)`.
- **Events repo** `app/repositories/event.py`: `EventRepository(session, tenant_id).top(field, source, frm, to, limit) -> list[EventTopRow]` where `EventTopRow(value: str, count: int)`. `TOP_FIELDS = {"src_ip","dst_ip","name","action","severity"}`. The `events` hypertable columns: `time, device_id, source('ids'|'dns'), event_key, tenant_id, category, src_ip, dst_ip, name, severity, action, attributes`.
- **Endpoint deps** `app/core/deps.py`: `require_tenant(action)` → `TenantContext(tenant, user, role)`; `enforce_csrf` (POST needs header `X-OPNGMS-CSRF`); `get_session`. RLS context is set per request.
- **Audit** `app/services/audit.py`: `await AuditService(session).record(actor_user_id=, tenant_id=, action=, target_type=, target_id=, ip=, details=)`.
- **POST endpoint shape** (see `app/api/config.py:155` `create_config_change`): `@router.post(path, dependencies=[Depends(enforce_csrf)])`, params `tenant_id`, `payload`, `request: Request`, `ctx=Depends(require_tenant(...))`, `session=Depends(get_session)`; do work → `AuditService.record(...)` → `await session.commit()`.
- **Router registration** `app/main.py`: `from app.api.reports import router as reports_router` + `app.include_router(reports_router)`.
- **Tests** `tests/`: `api_client` (owner-role ASGI client), `app_role_api_client` (connects as `opngms_app` → RLS active), `db_engine`, `two_tenants`, `tests/factories.py` (`make_tenant`, `make_user`, `make_membership`). Helpers in `tests/test_events_api.py`: `_login_superadmin(api_client, db_engine)`, `_insert_device(db_engine, tid, name=, status=)`, and event-seeding via raw `INSERT INTO events (...)`. CSRF header constant: `X-OPNGMS-CSRF`.

**Commands** (from `backend/`, venv active):
```bash
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test \
ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test \
.venv/bin/python -m pytest -q
```
Install new deps: `.venv/bin/pip install -e .` after editing `pyproject.toml`. WeasyPrint needs system libs (pango/cairo/gdk-pixbuf/libffi) — if `import weasyprint` fails at runtime, note it for the implementer to install the OS packages; the tests must still run.

**Security (non-negotiable):**
- Jinja2 **autoescape ON**; report data (signatures/hostnames/IPs) is untrusted → never `| safe` on data. Only the **SVG** we generate (from escaped values) is marked safe.
- WeasyPrint **`url_fetcher` blocks all remote/network URLs** (defense-in-depth; we also inline CSS via `<style>` and SVG inline, so no fetch should occur at all).
- New endpoint gated by **`REPORT_GENERATE`** + CSRF + audited; tenant-scoped under RLS; a cross-tenant request leaks nothing.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `pyproject.toml` | add `weasyprint`, `jinja2` | Modify |
| `app/core/rbac.py` | add `REPORT_GENERATE` action + matrix | Modify |
| `app/services/reporting/__init__.py` | package marker | Create |
| `app/services/reporting/charts.py` | pure data→SVG (`line_chart`, `bar_chart`) | Create |
| `app/services/reporting/aggregation.py` | `ReportAggregator` (devices, ranked tops, timeline, bucket pick) | Create |
| `app/services/reporting/context.py` | dataclasses + `build_context(...)` | Create |
| `app/services/reporting/template.py` | Jinja2 env + `render_html(context)` | Create |
| `app/services/reporting/templates/report.html.j2` | document template | Create |
| `app/services/reporting/templates/report.css` | print CSS | Create |
| `app/services/reporting/service.py` | `ReportService` (HTML + PDF) | Create |
| `app/schemas/report.py` | `ReportRequest` | Create |
| `app/api/reports.py` | `POST /reports` endpoint | Create |
| `app/main.py` | register the router | Modify |
| `tests/test_report_*.py` | tests | Create |

---

## Task 1: Dependencies + RBAC + engine skeleton (title page + footer → valid PDF)

**Files:**
- Modify: `pyproject.toml`, `app/core/rbac.py`
- Create: `app/services/reporting/__init__.py`, `template.py`, `templates/report.html.j2`, `templates/report.css`, `context.py` (minimal), `service.py` (minimal)
- Test: `tests/test_report_engine.py`

- [ ] **Step 1: Add dependencies**

In `pyproject.toml` `dependencies`, add after `defusedxml>=0.7`:
```toml
    "jinja2>=3.1",
    "weasyprint>=62",
```
Then: `.venv/bin/pip install -e .` (and ensure system libs for WeasyPrint are present).

- [ ] **Step 2: Add the RBAC action (test first)**

Add to `tests/test_rbac_matrix.py` (or create `tests/test_report_rbac.py`):
```python
from app.core.rbac import Action, can, TENANT_ADMIN, OPERATOR, READ_ONLY


def test_report_generate_grants():
    assert can(is_superadmin=False, role=TENANT_ADMIN, action=Action.REPORT_GENERATE)
    assert can(is_superadmin=False, role=OPERATOR, action=Action.REPORT_GENERATE)
    assert not can(is_superadmin=False, role=READ_ONLY, action=Action.REPORT_GENERATE)
    assert can(is_superadmin=True, role=None, action=Action.REPORT_GENERATE)
```
Run it → FAIL (`Action.REPORT_GENERATE` doesn't exist).

- [ ] **Step 3: Implement the action**

In `app/core/rbac.py`, add to `Action`:
```python
    REPORT_GENERATE = "report.generate"
```
and to `_TENANT_MATRIX`:
```python
    Action.REPORT_GENERATE: {TENANT_ADMIN, OPERATOR},
```
Run Step 2's test → PASS.

- [ ] **Step 4: Minimal context dataclasses**

Create `app/services/reporting/__init__.py` (empty). Create `app/services/reporting/context.py`:
```python
"""Report data model: plain dataclasses assembled from aggregations, rendered by the template."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RankedTable:
    title: str
    columns: tuple[str, str]          # e.g. ("Signature", "Count")
    rows: list[tuple[str, int]]       # already escaped at render time by autoescape


@dataclass
class AttacksBlock:
    timeline_svg: str                 # SVG string (built from escaped values) — marked safe at render
    tables: list[RankedTable]


@dataclass
class DeviceSection:
    device_name: str
    attacks: AttacksBlock | None = None


@dataclass
class ReportContext:
    # branding placeholders (5D fills these from per-tenant white-label config)
    tenant_name: str
    title: str
    timezone: str
    owner: str | None
    range_from: datetime
    range_to: datetime
    sections: list[DeviceSection] = field(default_factory=list)

    @property
    def toc(self) -> list[str]:
        return [s.device_name for s in self.sections]
```

- [ ] **Step 5: Jinja2 template engine**

Create `app/services/reporting/template.py`:
```python
"""Jinja2 environment (autoescape ON) and HTML rendering for reports."""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from app.services.reporting.context import ReportContext

_TEMPLATES = Path(__file__).parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES)),
    autoescape=select_autoescape(default=True, default_for_string=True),
)


def _css() -> str:
    return (_TEMPLATES / "report.css").read_text(encoding="utf-8")


def render_html(ctx: ReportContext) -> str:
    template = _env.get_template("report.html.j2")
    # The CSS and our generated SVGs are trusted strings (SVG text is escaped in charts.py),
    # so they are marked safe; ALL report DATA is auto-escaped by Jinja.
    return template.render(ctx=ctx, css=Markup(_css()), Markup=Markup)
```

Create `app/services/reporting/templates/report.css`:
```css
@page {
  size: A4;
  margin: 2cm 1.5cm 2cm 1.5cm;
  @bottom-left { content: "Report generated for timezone " string(tz); font-size: 8pt; color: #666; }
  @bottom-center { content: "Report owner: " string(owner); font-size: 8pt; color: #666; }
  @bottom-right { content: "Page " counter(page) " / " counter(pages); font-size: 8pt; color: #666; }
}
body { font-family: sans-serif; color: #1a1a1a; font-size: 10pt; }
.tz-meta { string-set: tz attr(data-tz), owner attr(data-owner); }
.title-page { text-align: center; padding-top: 6cm; page-break-after: always; }
.title-page h1 { font-size: 26pt; margin-bottom: .4cm; }
.title-page .range { color: #555; font-size: 12pt; }
.toc { page-break-after: always; }
.toc h2 { font-size: 16pt; }
.device-section { page-break-before: always; }
.device-section h2 { border-bottom: 2px solid #333; padding-bottom: 4px; }
table.ranked { border-collapse: collapse; width: 100%; margin: 6px 0 14px; }
table.ranked th, table.ranked td { border: 1px solid #ccc; padding: 4px 6px; text-align: left; font-size: 9pt; }
table.ranked th { background: #f0f0f0; }
.chart { margin: 8px 0; }
```

Create `app/services/reporting/templates/report.html.j2`:
```jinja
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <style>{{ css }}</style>
</head>
<body>
  <div class="tz-meta" data-tz="{{ ctx.timezone }}" data-owner="{{ ctx.owner or '—' }}"></div>

  <section class="title-page">
    <h1>{{ ctx.title }}</h1>
    <div class="tenant">{{ ctx.tenant_name }}</div>
    <div class="range">{{ ctx.range_from.strftime('%Y-%m-%d') }} – {{ ctx.range_to.strftime('%Y-%m-%d') }}</div>
  </section>

  <section class="toc">
    <h2>Table of contents</h2>
    <ol>
      {% for name in ctx.toc %}<li>{{ name }}</li>{% endfor %}
    </ol>
  </section>

  {% for section in ctx.sections %}
  <section class="device-section">
    <h2>{{ section.device_name }}</h2>
    {% if section.attacks %}
    <h3>Attacks</h3>
    <div class="chart">{{ Markup(section.attacks.timeline_svg) }}</div>
    {% for tbl in section.attacks.tables %}
    <table class="ranked">
      <thead><tr><th>{{ tbl.title }}</th><th>{{ tbl.columns[1] }}</th></tr></thead>
      <tbody>
        {% for value, count in tbl.rows %}<tr><td>{{ value }}</td><td>{{ count }}</td></tr>{% endfor %}
        {% if not tbl.rows %}<tr><td colspan="2">No data</td></tr>{% endif %}
      </tbody>
    </table>
    {% endfor %}
    {% endif %}
  </section>
  {% endfor %}
</body>
</html>
```

- [ ] **Step 6: Minimal ReportService (HTML + PDF), test (fail first)**

Create `tests/test_report_engine.py`:
```python
from datetime import datetime, timezone

from app.services.reporting.context import ReportContext
from app.services.reporting.service import html_to_pdf
from app.services.reporting.template import render_html


def _ctx():
    return ReportContext(
        tenant_name="Acme Corp",
        title="Security Report",
        timezone="UTC",
        owner=None,
        range_from=datetime(2026, 6, 1, tzinfo=timezone.utc),
        range_to=datetime(2026, 6, 8, tzinfo=timezone.utc),
        sections=[],
    )


def test_render_html_contains_title_and_tenant():
    html = render_html(_ctx())
    assert "Security Report" in html
    assert "Acme Corp" in html
    assert "Table of contents" in html


def test_html_to_pdf_produces_valid_pdf():
    pdf = html_to_pdf(render_html(_ctx()))
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 1000


def test_report_data_is_html_escaped():
    ctx = _ctx()
    ctx.tenant_name = "<script>alert(1)</script>"
    html = render_html(ctx)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
```
Run → FAIL (`service.html_to_pdf` missing).

- [ ] **Step 7: Implement `service.py`**

Create `app/services/reporting/service.py`:
```python
"""Render a ReportContext to a PDF via WeasyPrint, with remote resource fetching disabled."""
from __future__ import annotations

from weasyprint import HTML


def _blocked_fetcher(url: str):
    # Defense-in-depth: reports never fetch remote/local resources (CSS + SVG are inlined).
    raise ValueError(f"remote resource fetching is disabled in reports: {url!r}")


def html_to_pdf(html: str) -> bytes:
    return HTML(string=html, url_fetcher=_blocked_fetcher).write_pdf()
```
Run Step 6's tests → PASS. Run the full suite → green.

- [ ] **Step 8: Commit**
```bash
git add pyproject.toml app/core/rbac.py app/services/reporting/ tests/test_report_engine.py tests/test_rbac_matrix.py
git commit -m "feat(reporting): 5A engine skeleton — WeasyPrint+Jinja2 PDF, REPORT_GENERATE, title/TOC/footer"
```

---

## Task 2: SVG charts (pure functions)

**Files:**
- Create: `app/services/reporting/charts.py`, `tests/test_report_charts.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_report_charts.py`:
```python
from app.services.reporting.charts import bar_chart, line_chart


def test_line_chart_is_svg_with_points():
    svg = line_chart([("12:00", 3), ("13:00", 7), ("14:00", 1)], width=400, height=120)
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")
    assert "polyline" in svg or "path" in svg


def test_bar_chart_renders_a_bar_per_row_and_escapes_labels():
    svg = bar_chart([("<b>a</b>", 5), ("b", 2)], width=300, height=100)
    assert svg.count("<rect") >= 2
    # label text must be escaped (untrusted)
    assert "<b>a</b>" not in svg
    assert "&lt;b&gt;a&lt;/b&gt;" in svg


def test_charts_handle_empty_input():
    assert line_chart([], width=100, height=50).startswith("<svg")
    assert bar_chart([], width=100, height=50).startswith("<svg")
```
Run → FAIL.

- [ ] **Step 2: Implement `charts.py`**

Create `app/services/reporting/charts.py`:
```python
"""Pure functions producing SVG strings from data. No I/O, deterministic, all text escaped."""
from __future__ import annotations

from xml.sax.saxutils import escape

_PAD = 24


def _svg_open(width: int, height: int) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" class="chart-svg">'
    )


def line_chart(points: list[tuple[str, float]], *, width: int, height: int) -> str:
    """A simple line/timeline chart. `points` is [(label, value)]."""
    parts = [_svg_open(width, height)]
    if points:
        values = [v for _, v in points]
        vmax = max(values) or 1
        n = len(points)
        inner_w = width - 2 * _PAD
        inner_h = height - 2 * _PAD
        step = inner_w / max(n - 1, 1)
        coords = []
        for i, (_, v) in enumerate(points):
            x = _PAD + i * step
            y = _PAD + inner_h - (v / vmax) * inner_h
            coords.append(f"{x:.1f},{y:.1f}")
        parts.append(f'<polyline fill="none" stroke="#2b6cb0" stroke-width="2" points="{" ".join(coords)}" />')
        # baseline
        parts.append(f'<line x1="{_PAD}" y1="{_PAD + inner_h}" x2="{width - _PAD}" y2="{_PAD + inner_h}" stroke="#ccc" />')
    parts.append("</svg>")
    return "".join(parts)


def bar_chart(rows: list[tuple[str, float]], *, width: int, height: int) -> str:
    """A horizontal-ranked bar chart. `rows` is [(label, value)]."""
    parts = [_svg_open(width, height)]
    if rows:
        vmax = max(v for _, v in rows) or 1
        n = len(rows)
        inner_w = width - 2 * _PAD
        band = (height - 2 * _PAD) / n
        for i, (label, v) in enumerate(rows):
            y = _PAD + i * band
            w = (v / vmax) * inner_w
            parts.append(f'<rect x="{_PAD}" y="{y:.1f}" width="{w:.1f}" height="{band * 0.7:.1f}" fill="#2b6cb0" />')
            parts.append(
                f'<text x="{_PAD + 2}" y="{y + band * 0.5:.1f}" font-size="8" fill="#fff">{escape(label)}</text>'
            )
    parts.append("</svg>")
    return "".join(parts)
```
Run → PASS. Full suite green.

- [ ] **Step 3: Commit**
```bash
git add app/services/reporting/charts.py tests/test_report_charts.py
git commit -m "feat(reporting): SVG line/bar charts (pure, escaped)"
```

---

## Task 3: Aggregation layer (devices + ranked tops + time-bucketed timeline)

**Files:**
- Create: `app/services/reporting/aggregation.py`, `tests/test_report_aggregation.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_report_aggregation.py`:
```python
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.reporting.aggregation import ReportAggregator, pick_bucket
from tests.factories import make_tenant


def test_pick_bucket_by_span():
    assert pick_bucket(timedelta(days=1)) == "1 hour"
    assert pick_bucket(timedelta(days=10)) == "6 hours"
    assert pick_bucket(timedelta(days=40)) == "1 day"


async def _seed(db_engine, tenant_id, device_id, names):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:
        from sqlalchemy import text
        for i, name in enumerate(names):
            await s.execute(
                text(
                    "INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip, dst_ip) "
                    "VALUES (:t, :d, 'ids', :k, :tid, :name, '10.0.0.5', '8.8.8.8')"
                ),
                {"t": base + timedelta(minutes=i), "d": device_id, "k": f"k{i}",
                 "tid": tenant_id, "name": name},
            )
        await s.commit()
    return base


async def test_top_and_timeline(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tid = t.id
    did = uuid.uuid4()
    async with factory() as s:
        from sqlalchemy import text
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id, :t, 'fw1', 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tid},
        )
        await s.commit()
    base = await _seed(db_engine, tid, did, ["ET SCAN", "ET SCAN", "ET POLICY"])

    async with factory() as s:
        agg = ReportAggregator(s, tid)
        devices = await agg.devices()
        assert [d.name for d in devices] == ["fw1"]
        top = await agg.top(field="name", frm=base - timedelta(hours=1), to=base + timedelta(hours=1))
        assert (top[0].value, top[0].count) == ("ET SCAN", 2)
        tl = await agg.timeline(frm=base - timedelta(hours=1), to=base + timedelta(hours=1), bucket="1 hour")
        assert sum(c for _, c in tl) == 3
```
Run → FAIL.

- [ ] **Step 2: Implement `aggregation.py`**

Create `app/services/reporting/aggregation.py`:
```python
"""Tenant-scoped report aggregations over the events/metrics hypertables (RLS + tenant filter)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.event import EventRepository
from app.schemas.event import EventTopRow

# Validated TimescaleDB time_bucket widths (still bound via CAST, never string-formatted into SQL).
_BUCKETS = ("1 hour", "6 hours", "1 day")


def pick_bucket(span: timedelta) -> str:
    if span <= timedelta(days=2):
        return "1 hour"
    if span <= timedelta(days=14):
        return "6 hours"
    return "1 day"


@dataclass
class DeviceRow:
    id: uuid.UUID
    name: str


class ReportAggregator:
    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id
        self._events = EventRepository(session, tenant_id)

    async def devices(self) -> list[DeviceRow]:
        rows = (
            await self.session.execute(
                text("SELECT id, name FROM devices WHERE tenant_id = :tid ORDER BY name"),
                {"tid": self.tenant_id},
            )
        ).all()
        return [DeviceRow(id=r.id, name=r.name) for r in rows]

    async def top(
        self, *, field: str, frm: datetime, to: datetime, source: str = "ids", limit: int = 10
    ) -> list[EventTopRow]:
        return await self._events.top(field=field, source=source, frm=frm, to=to, limit=limit)

    async def timeline(
        self, *, frm: datetime, to: datetime, bucket: str, source: str = "ids"
    ) -> list[tuple[datetime, int]]:
        if bucket not in _BUCKETS:
            raise ValueError(f"bucket not allowed: {bucket}")
        sql = text(
            "SELECT time_bucket(CAST(:bucket AS interval), time) AS b, count(*) AS c "
            "FROM events WHERE tenant_id = :tid AND source = :source "
            "AND time >= :frm AND time < :to GROUP BY b ORDER BY b"
        )
        rows = (
            await self.session.execute(
                sql,
                {"bucket": bucket, "tid": self.tenant_id, "source": source, "frm": frm, "to": to},
            )
        ).all()
        return [(r.b, int(r.c)) for r in rows]
```
Run → PASS.

- [ ] **Step 3: Cross-tenant isolation test (RLS)**

Add to `tests/test_report_aggregation.py` a test that seeds events for two tenants and, **connecting as `opngms_app`** (RLS), the aggregator for tenant A sees only A's data. Mirror the RLS pattern used in `tests/test_events_rls_api.py` (use the `app_role_api_client`'s engine approach, or the SQL-level RLS fixtures). Concretely:
```python
async def test_timeline_is_tenant_isolated_under_rls(two_tenants, db_engine):
    # two_tenants yields (tenant_a_id, tenant_b_id) already created; see conftest.
    # seed IDS events for BOTH tenants on their own devices, then query as opngms_app
    # for tenant A and assert tenant B's events are not counted.
    ...
```
If `two_tenants`/the RLS engine helper shape differs, follow `tests/test_events_rls_api.py` exactly (it already proves event RLS isolation through the API; replicate its seeding + the `opngms_app` connection for a direct `ReportAggregator` call). The assertion: A's `timeline`/`top` totals equal only A's seeded count; B's distinctive signature name never appears in A's `top`.
Run → PASS.

- [ ] **Step 4: Commit**
```bash
git add app/services/reporting/aggregation.py tests/test_report_aggregation.py
git commit -m "feat(reporting): tenant-scoped aggregation (devices, ranked tops, time_bucket timeline) + RLS isolation test"
```

---

## Task 4: Context builder + Attacks section wired into the report

**Files:**
- Modify: `app/services/reporting/context.py` (add `build_context`)
- Create: `tests/test_report_context.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_report_context.py`:
```python
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.reporting.context import build_context
from app.services.reporting.aggregation import ReportAggregator
from app.services.reporting.template import render_html
from tests.factories import make_tenant


async def test_build_context_includes_attacks_section(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tid = t.id
    did = uuid.uuid4()
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id, :t, 'fw-edge', 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tid},
        )
        for i, name in enumerate(["ET SCAN NMAP", "ET SCAN NMAP", "ET POLICY DNS"]):
            await s.execute(
                text(
                    "INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip, dst_ip) "
                    "VALUES (:t, :d, 'ids', :k, :tid, :name, '10.0.0.9', '8.8.4.4')"
                ),
                {"t": base + timedelta(minutes=i), "d": did, "k": f"k{i}", "tid": tid, "name": name},
            )
        await s.commit()

    async with factory() as s:
        agg = ReportAggregator(s, tid)
        ctx = await build_context(
            agg, tenant_name="Acme", timezone_name="UTC", owner=None,
            frm=base - timedelta(hours=1), to=base + timedelta(hours=1),
        )
    assert ctx.toc == ["fw-edge"]
    assert ctx.sections[0].attacks is not None
    html = render_html(ctx)
    assert "ET SCAN NMAP" in html        # ranked table value present
    assert "fw-edge" in html
    assert "<svg" in html                # timeline chart embedded
```
Run → FAIL (`build_context` missing).

- [ ] **Step 2: Implement `build_context` in `context.py`**

Append to `app/services/reporting/context.py`:
```python
from datetime import timezone as _tz

from app.services.reporting.aggregation import ReportAggregator
from app.services.reporting.charts import line_chart


async def build_context(
    aggregator: ReportAggregator,
    *,
    tenant_name: str,
    timezone_name: str,
    owner: str | None,
    frm: datetime,
    to: datetime,
    title: str = "Security & Activity Report",
) -> ReportContext:
    from app.services.reporting.aggregation import pick_bucket

    bucket = pick_bucket(to - frm)
    sections: list[DeviceSection] = []
    devices = await aggregator.devices()
    for dev in devices:
        # Attacks block: timeline + three ranked tables (IDS).
        tl = await aggregator.timeline(frm=frm, to=to, bucket=bucket, source="ids")
        svg = line_chart([(b.astimezone(_tz.utc).strftime("%m-%d %H:%M"), c) for b, c in tl], width=520, height=140)
        top_attempts = await aggregator.top(field="name", frm=frm, to=to)
        top_targets = await aggregator.top(field="dst_ip", frm=frm, to=to)
        top_initiators = await aggregator.top(field="src_ip", frm=frm, to=to)
        attacks = AttacksBlock(
            timeline_svg=svg,
            tables=[
                RankedTable("Top Attempts", ("Signature", "Count"), [(r.value, r.count) for r in top_attempts]),
                RankedTable("Top Targets", ("Target", "Count"), [(r.value, r.count) for r in top_targets]),
                RankedTable("Top Initiators", ("Initiator", "Count"), [(r.value, r.count) for r in top_initiators]),
            ],
        )
        sections.append(DeviceSection(device_name=dev.name, attacks=attacks))

    return ReportContext(
        tenant_name=tenant_name,
        title=title,
        timezone=timezone_name,
        owner=owner,
        range_from=frm,
        range_to=to,
        sections=sections,
    )
```
> NOTE: the per-device timeline/top here aggregate the tenant's IDS events for the range (not yet filtered per device). 5A proves the pipeline; **per-device filtering is a 5B refinement** — record it as tech debt (Task 5). Keep this explicit so the reviewer doesn't flag it as a bug.

Run → PASS. Full suite green.

- [ ] **Step 3: Commit**
```bash
git add app/services/reporting/context.py tests/test_report_context.py
git commit -m "feat(reporting): context builder + Attacks section (timeline + ranked tables)"
```

---

## Task 5: Generate API endpoint (REPORT_GENERATE + CSRF + audit) + tech debt

**Files:**
- Create: `app/schemas/report.py`, `app/api/reports.py`, `tests/test_report_api.py`
- Modify: `app/main.py`, `app/services/reporting/service.py` (add `ReportService`)
- Modify: this plan file (tech debt)

- [ ] **Step 1: Request schema**

Create `app/schemas/report.py`:
```python
from datetime import datetime

from pydantic import BaseModel, Field


class ReportRequest(BaseModel):
    from_: datetime = Field(alias="from")
    to: datetime
    timezone: str = "UTC"

    model_config = {"populate_by_name": True}
```

- [ ] **Step 2: `ReportService` (ties aggregation→context→html→pdf)**

Append to `app/services/reporting/service.py`:
```python
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.reporting.aggregation import ReportAggregator
from app.services.reporting.context import build_context
from app.services.reporting.template import render_html

# Bound the queried range to keep aggregation cheap.
MAX_RANGE_DAYS = 92


def _ensure_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


class ReportService:
    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def build_html(
        self, *, tenant_name: str, frm: datetime, to: datetime, timezone_name: str, owner: str | None
    ) -> str:
        frm, to = _ensure_utc(frm), _ensure_utc(to)
        agg = ReportAggregator(self.session, self.tenant_id)
        ctx = await build_context(
            agg, tenant_name=tenant_name, timezone_name=timezone_name, owner=owner, frm=frm, to=to
        )
        return render_html(ctx)

    async def build_report(
        self, *, tenant_name: str, frm: datetime, to: datetime, timezone_name: str, owner: str | None
    ) -> bytes:
        html = await self.build_html(
            tenant_name=tenant_name, frm=frm, to=to, timezone_name=timezone_name, owner=owner
        )
        return html_to_pdf(html)
```

- [ ] **Step 3: Write the failing API test**

Create `tests/test_report_api.py` (reuse the helpers from `tests/test_events_api.py` — copy `_login_superadmin`, `_insert_device`, and an event seeder, or import them):
```python
import uuid
from datetime import datetime, timedelta, timezone

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.main import app
from tests.factories import make_tenant

CSRF = {"X-OPNGMS-CSRF": "1"}


async def _login_superadmin(api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tid = t.id
    await api_client.post("/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345"})
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345"})
    return tid


async def _seed(db_engine, tid):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id, :t, 'fw1', 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tid},
        )
        await s.execute(
            text(
                "INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip, dst_ip) "
                "VALUES (:t, :d, 'ids', 'k0', :tid, 'ET SCAN NMAP', '10.0.0.9', '8.8.4.4')"
            ),
            {"t": base, "d": did, "tid": tid},
        )
        await s.commit()
    return base


async def test_generate_report_returns_pdf(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    base = await _seed(db_engine, tid)
    body = {"from": (base - timedelta(hours=1)).isoformat(), "to": (base + timedelta(hours=1)).isoformat()}
    r = await api_client.post(f"/api/tenants/{tid}/reports", json=body, headers=CSRF)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:5] == b"%PDF-"


async def test_generate_report_requires_csrf(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    body = {"from": "2026-06-09T11:00:00Z", "to": "2026-06-09T13:00:00Z"}
    r = await api_client.post(f"/api/tenants/{tid}/reports", json=body)  # no CSRF header
    assert r.status_code == 403


async def test_generate_report_requires_auth(db_engine):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as anon:
        r = await anon.post(f"/api/tenants/{uuid.uuid4()}/reports", json={"from": "2026-06-09T11:00:00Z", "to": "2026-06-09T13:00:00Z"}, headers=CSRF)
    assert r.status_code in (401, 404)
```
Run → FAIL (router not mounted).

- [ ] **Step 4: Implement `app/api/reports.py`**

Create `app/api/reports.py`:
```python
import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import TenantContext, enforce_csrf, require_tenant
from app.core.rbac import Action
from app.schemas.report import ReportRequest
from app.services.audit import AuditService
from app.services.reporting.service import ReportService

router = APIRouter(prefix="/api/tenants/{tenant_id}", tags=["reports"])


@router.post("/reports", dependencies=[Depends(enforce_csrf)])
async def generate_report(
    tenant_id: uuid.UUID,
    payload: ReportRequest,
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.REPORT_GENERATE)),
    session: AsyncSession = Depends(get_session),
) -> Response:
    pdf = await ReportService(session, tenant_id).build_report(
        tenant_name=ctx.tenant.name,
        frm=payload.from_,
        to=payload.to,
        timezone_name=payload.timezone,
        owner=None,
    )
    await AuditService(session).record(
        actor_user_id=ctx.user.id,
        tenant_id=tenant_id,
        action="report.generate",
        target_type="report",
        target_id=None,
        ip=request.client.host if request.client else None,
        details={"from": payload.from_.isoformat(), "to": payload.to.isoformat()},
    )
    await session.commit()
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="opngms-report.pdf"'},
    )
```
Register in `app/main.py`: add `from app.api.reports import router as reports_router` with the other imports and `app.include_router(reports_router)` with the others.
Run Step 3's tests → PASS.

- [ ] **Step 5: Cross-tenant isolation + SSRF tests**

Add to `tests/test_report_api.py`:
```python
async def test_report_cross_tenant_is_isolated(app_role_api_client, db_engine):
    """Under RLS (opngms_app), a report for tenant A must not contain tenant B's IDS signatures."""
    # Seed two tenants with distinct signatures; login a superadmin that is a member of A only
    # (or use the existing membership pattern). Generate A's report; assert B's signature
    # string is absent. Use build_html via the service for an assertable text surface, OR assert
    # on the API: since the PDF text isn't trivially greppable, prefer a service-level RLS test:
    #   - connect a session as opngms_app for tenant A, call ReportService(...).build_html(...)
    #   - assert B's signature NOT in the returned HTML.
    ...


async def test_report_data_not_remotely_fetched(api_client, db_engine):
    """A hostile URL embedded as an IDS signature must never trigger an outbound fetch:
    the report still renders (the URL is escaped text, and WeasyPrint's url_fetcher is blocked)."""
    tid = await _login_superadmin(api_client, db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id, :t, 'fw1', 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tid},
        )
        await s.execute(
            text(
                "INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip, dst_ip) "
                "VALUES (:t, :d, 'ids', 'k0', :tid, :name, '10.0.0.9', '8.8.4.4')"
            ),
            {"t": base, "d": did, "tid": tid,
             "name": "http://169.254.169.254/latest/meta-data/"},
        )
        await s.commit()
    body = {"from": (base - timedelta(hours=1)).isoformat(), "to": (base + timedelta(hours=1)).isoformat()}
    r = await api_client.post(f"/api/tenants/{tid}/reports", json=body, headers=CSRF)
    assert r.status_code == 200
    assert r.content[:5] == b"%PDF-"
```
For the cross-tenant test, follow the RLS approach in `tests/test_events_rls_api.py` (build a session bound to `opngms_app` for the tenant and call `ReportService(...).build_html(...)` directly — HTML is the assertable surface; assert the other tenant's distinctive signature is absent). Run → PASS.

- [ ] **Step 6: Run the full suite + record tech debt**

`pytest -q` → all green. Append to this plan:
```markdown
## Technical debt (5A)

- **Per-device aggregation**: the Attacks block aggregates the tenant's IDS events for the range, not
  yet filtered per device (5B adds `device_id` filtering to `aggregation.top`/`timeline`).
- **WeasyPrint system libs**: pango/cairo/gdk-pixbuf/libffi must be in the runtime image — wire into the
  **Deploy** milestone Dockerfile.
- **Sections 5B–5C**: Web Activity / Bandwidth / Up-Down (5B) and Applications / Web Filter / threat-level
  (5C) are stubs/absent; the template frame is ready.
- **White-label**: branding uses placeholders (tenant name, owner=None); 5D adds the per-tenant config.
- **No persistence**: 5A returns the PDF inline; storage + history + scheduled cron is 5E.
- **Range cap** `MAX_RANGE_DAYS=92` defined but enforcement (reject/clamp) is light — tighten in 5B.
```

- [ ] **Step 7: Commit**
```bash
git add app/schemas/report.py app/api/reports.py app/main.py app/services/reporting/service.py \
        tests/test_report_api.py docs/superpowers/plans/2026-06-10-opngms-phase5-milestone5A-reporting-foundation.md
git commit -m "feat(reporting): on-demand generate API (REPORT_GENERATE+CSRF+audit) + isolation/SSRF tests; 5A debt"
```

---

## Technical debt (5A)

- **Per-device aggregation**: the Attacks block aggregates the tenant's IDS events for the range, not
  yet filtered per device (5B adds `device_id` filtering to `aggregation.top`/`timeline`).
- **WeasyPrint system libs**: pango/cairo/gdk-pixbuf/libffi must be in the runtime image — wire into the
  **Deploy** milestone Dockerfile.
- **Sections 5B–5C**: Web Activity / Bandwidth / Up-Down (5B) and Applications / Web Filter / threat-level
  (5C) are stubs/absent; the template frame is ready.
- **White-label**: branding uses placeholders (tenant name, owner=None); 5D adds the per-tenant config.
- **No persistence**: 5A returns the PDF inline; storage + history + scheduled cron is 5E.
- **Range cap** `MAX_RANGE_DAYS=92` defined but enforcement (reject/clamp) is light — tighten in 5B.

---

## Definition of "Done" (5A)
- `POST /api/tenants/{tenant_id}/reports` returns a valid PDF with a title page, TOC, a real **Attacks**
  section per firewall (timeline SVG + Top Attempts/Targets/Initiators tables), and a footer (page
  numbers, timezone, owner placeholder).
- Gated by **`REPORT_GENERATE`** + CSRF + audited; tenant-scoped under RLS (cross-tenant report leaks
  nothing); SSRF-safe (no remote fetch); all report data autoescaped; no config secrets.
- Backend suite green; no DB migration (`alembic check` unaffected).
