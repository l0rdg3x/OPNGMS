# OPNGMS — Phase 5 / Milestone 5F: Report Readability — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Give the report's timeline charts labelled X/Y axes with units + value ticks, and add a short plain-language explanation under each section, so a non-technical customer can read the report.

**Architecture:** Enhance the pure `charts.line_chart` (axes/ticks/gridlines/units + an internal `y_format` callable) and pass per-chart units/labels from `context.py` + `mock_sections.py`; add static `.explain` paragraphs to the Jinja template. No data/aggregation changes; still escaped + secret-safe.

**Tech Stack:** Python 3.12+ (hand-built SVG), Jinja2; pytest.

---

## Context for the implementer (read first)

- `app/services/reporting/charts.py` currently has `line_chart(points, *, width, height)` (polyline + a baseline only, no axes) and `bar_chart` (unused on the data path — leave it). All emitted text is escaped via `xml.sax.saxutils.escape`.
- `app/services/reporting/context.py` `build_context` calls `line_chart(...)` for: Attacks timeline (line ~98), Web Activity timeline (~118), Data Usage timeline (~131), Up/Down availability (~138). It has `human_bytes(n)` already. The SVG strings are rendered via `{{ Markup(...) }}` in the template (autoescape stays on for data).
- `app/services/reporting/mock_sections.py` has `_timeline_svg(seed, *, height=140)` used by `applications_block`/`web_filter_block`.
- `app/services/reporting/templates/report.html.j2` renders each device section with `<h3>` headings (Attacks / Web Activity / Data Usage / Up/Down Status / Applications / Web Filter); `report.css` has the styles.
- Tests: `tests/test_report_charts.py` (pure), `tests/test_report_context.py` (DB-backed render).

**Commands** (from `backend/`):
```
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test \
ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test \
.venv/bin/python -m pytest -q
```

**Security:** `line_chart` must keep escaping every emitted text (axis labels, tick values, time labels). The `y_format` callable is internal (never user data).

---

## Task 1: Enhance `line_chart` with axes + units

**Files:** Modify `app/services/reporting/charts.py`; Modify `tests/test_report_charts.py`.

- [ ] **Step 1: Update the failing tests** — in `tests/test_report_charts.py`, keep the existing `line_chart` tests working (it still returns `<svg…>…</svg>` with a `polyline`), and ADD assertions:
```python
def test_line_chart_has_axes_units_and_ticks():
    svg = line_chart(
        [("12:00", 1024), ("13:00", 4096), ("14:00", 2048)],
        width=400, height=140, y_label="Data", x_label="Time",
        y_format=lambda v: f"{v/1024:.1f} KB",
    )
    assert "Data" in svg and "Time" in svg          # axis titles
    assert "KB" in svg                              # formatted Y tick (units)
    assert "12:00" in svg                           # X tick label
    assert svg.count("<line") >= 2                  # at least the two axis lines

def test_line_chart_empty_shows_no_data():
    svg = line_chart([], width=200, height=100)
    assert svg.startswith("<svg") and "No data" in svg

def test_line_chart_escapes_x_labels():
    svg = line_chart([("<b>x</b>", 5)], width=200, height=100)
    assert "<b>x</b>" not in svg and "&lt;b&gt;x&lt;/b&gt;" in svg
```
Run → FAIL.

- [ ] **Step 2: Rewrite `line_chart`** in `app/services/reporting/charts.py` (keep `_svg_open`, `bar_chart`, and the `escape` import; add `from collections.abc import Callable`):
```python
# Axis margins (left for Y labels, bottom for X labels, small top/right).
_ML, _MR, _MT, _MB = 48, 12, 12, 30


def _int_fmt(v: float) -> str:
    return f"{v:.0f}"


def line_chart(
    points: list[tuple[str, float]],
    *,
    width: int,
    height: int,
    y_label: str = "",
    x_label: str = "Time",
    y_format: Callable[[float], str] | None = None,
) -> str:
    """A time-series line chart with labelled X/Y axes, value ticks and gridlines.

    `points` is [(x_label, value)] (x_labels are time buckets). `y_format` formats the Y tick values
    (e.g. human-readable bytes); it is an INTERNAL callable, never user-controlled. All text is escaped.
    """
    fmt = y_format or _int_fmt
    plot_w = width - _ML - _MR
    plot_h = height - _MT - _MB
    x0 = _ML
    y0 = _MT + plot_h  # bottom-left origin of the plot area
    parts = [_svg_open(width, height)]

    if not points:
        parts.append(
            f'<text x="{width / 2:.0f}" y="{height / 2:.0f}" font-size="10" fill="#888" '
            f'text-anchor="middle">No data</text></svg>'
        )
        return "".join(parts)

    values = [v for _, v in points]
    vmax = max(values) or 1
    n = len(points)
    step = plot_w / max(n - 1, 1)

    # Y gridlines + value ticks (0 .. vmax).
    ticks = 4
    for t in range(ticks + 1):
        val = vmax * t / ticks
        gy = y0 - (val / vmax) * plot_h
        parts.append(
            f'<line x1="{x0}" y1="{gy:.1f}" x2="{x0 + plot_w}" y2="{gy:.1f}" stroke="#eee" stroke-width="1" />'
        )
        label = fmt(val)
        if label:
            parts.append(
                f'<text x="{x0 - 4}" y="{gy + 3:.1f}" font-size="7" fill="#666" '
                f'text-anchor="end">{escape(label)}</text>'
            )

    # Axes.
    parts.append(f'<line x1="{x0}" y1="{_MT}" x2="{x0}" y2="{y0}" stroke="#999" stroke-width="1" />')
    parts.append(f'<line x1="{x0}" y1="{y0}" x2="{x0 + plot_w}" y2="{y0}" stroke="#999" stroke-width="1" />')

    # X tick labels (thin to ~6 to avoid crowding; always label the last point).
    max_labels = 6
    every = max(1, (n + max_labels - 1) // max_labels)
    for i, (lab, _v) in enumerate(points):
        if i % every != 0 and i != n - 1:
            continue
        x = x0 + i * step
        parts.append(
            f'<text x="{x:.1f}" y="{y0 + 12}" font-size="7" fill="#666" '
            f'text-anchor="middle">{escape(lab)}</text>'
        )

    # Data polyline + point markers.
    coords = []
    for i, (_lab, v) in enumerate(points):
        x = x0 + i * step
        y = y0 - (v / vmax) * plot_h
        coords.append((x, y))
    pts_attr = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    parts.append(f'<polyline fill="none" stroke="#2b6cb0" stroke-width="2" points="{pts_attr}" />')
    for x, y in coords:
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.8" fill="#2b6cb0" />')

    # Axis titles.
    if x_label:
        parts.append(
            f'<text x="{x0 + plot_w / 2:.0f}" y="{height - 3}" font-size="8" fill="#444" '
            f'text-anchor="middle">{escape(x_label)}</text>'
        )
    if y_label:
        ty = _MT + plot_h / 2
        parts.append(
            f'<text x="10" y="{ty:.0f}" font-size="8" fill="#444" text-anchor="middle" '
            f'transform="rotate(-90 10 {ty:.0f})">{escape(y_label)}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)
```
Run Step 1 → PASS. (Confirm the existing `test_line_chart_is_svg_with_points` / escaping tests still pass — adjust them only if they asserted the old no-axis structure.)

- [ ] **Step 3: Commit**
```bash
git add app/services/reporting/charts.py tests/test_report_charts.py
git commit -m "feat(reporting): line_chart with labelled X/Y axes, value ticks, units"
```

---

## Task 2: Wire per-chart units + add plain-language explanations

**Files:** Modify `app/services/reporting/context.py`, `app/services/reporting/mock_sections.py`, `app/services/reporting/templates/report.html.j2`, `app/services/reporting/templates/report.css`; Modify `tests/test_report_context.py`.

- [ ] **Step 1: Per-chart units in `context.py`** — add two small internal formatters near `human_bytes`:
```python
def _updown_fmt(v: float) -> str:
    if v >= 0.99:
        return "Up"
    if v <= 0.01:
        return "Down"
    return ""
```
Update the `line_chart(...)` calls in `build_context`:
- Attacks timeline: add `y_label="Attempts", x_label="Time"` (default integer ticks).
- Web Activity timeline: add `y_label="DNS lookups", x_label="Time"`.
- Data Usage timeline: add `y_label="Data / period", x_label="Time", y_format=human_bytes`.
- Up/Down availability: add `y_label="Status", x_label="Time", y_format=_updown_fmt`.

- [ ] **Step 2: Mock timelines** — in `mock_sections.py`, give `_timeline_svg` a `y_label` param and pass it:
```python
def _timeline_svg(seed: int, *, height: int = 140, y_label: str = "Sessions") -> str:
    pts = [(f"t{i}", 5 + (seed >> (i % 6)) % 40 + (i % 3) * 3) for i in range(6)]
    return line_chart(pts, width=520, height=height, y_label=y_label, x_label="Time")
```
`applications_block` → `_timeline_svg(seed, y_label="Sessions")`; `web_filter_block` → `_timeline_svg(seed, y_label="Requests")`.

- [ ] **Step 3: Explanations in the template** — in `report.html.j2`, add a `<p class="explain">…</p>` immediately after each section `<h3>` with the exact copy (from the spec §4):
  - Attacks: "Attempted intrusions your firewall's threat detection blocked during this period. The chart shows how many attempts occurred over time; the tables list the most frequent attack types, which of your devices were targeted, and where the attempts came from."
  - Web Activity: "The websites and online services your network looked up. The chart shows lookup volume over time; the tables show the most-visited sites, the busiest devices, and the domains that were blocked."
  - Data Usage: "How much data flowed through your firewall over time (incoming plus outgoing). The totals below summarise the whole period."
  - Up/Down Status: "Whether this firewall was online and reachable over the period. 'Uptime' is the share of time it was online — higher is better."
  - Applications (after the existing sample-note): "Applications seen on your network, each with a simple risk rating — green (Low), blue (Guarded), orange (High). These figures are sample data until application monitoring is enabled."
  - Web Filter: "Categories of web content requested from your network, each with a risk rating. These figures are sample data until content categorisation is enabled."
  (These are static, trusted strings — fine to write literally in the template.)

- [ ] **Step 4: CSS** — add to `report.css`:
```css
.explain { font-size: 8pt; color: #555; margin: 1px 0 7px; max-width: 17cm; line-height: 1.3; }
```

- [ ] **Step 5: Render test** — in `tests/test_report_context.py`, extend the web/bandwidth/status render test (or add one) to assert the rendered HTML contains an explanation phrase (e.g. `"How much data flowed through"`) and an axis unit (e.g. `"DNS lookups"` and `"Attempts"`). Run the targeted + full suite green.

- [ ] **Step 6: Commit**
```bash
git add app/services/reporting/context.py app/services/reporting/mock_sections.py \
        app/services/reporting/templates/report.html.j2 app/services/reporting/templates/report.css \
        tests/test_report_context.py
git commit -m "feat(reporting): per-chart axis units + plain-language section explanations"
```

---

## Task 3: Technical debt + sample render
- [ ] **Step 1:** Append 5F debt: charts are static SVG (no interactivity); explanation copy is English-only; the availability chart uses a 0/1 line with Down/Up ticks (a status band would be richer).
- [ ] **Step 2:** Commit `docs: technical debt milestone 5F`.

---

## Definition of "Done" (5F)
- Timeline charts show labelled X (Time) + Y (units) axes with value ticks/gridlines; the Data Usage chart's Y ticks are human bytes (KB/MB/GB); the availability chart shows Down/Up.
- Each section has a short plain-language explanation; all text escaped/secret-safe; backend suite green; a rendered sample PDF confirms the look.
