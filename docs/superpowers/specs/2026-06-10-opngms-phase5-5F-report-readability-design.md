# OPNGMS — Phase 5 / Milestone 5F: Report Readability (chart axes + units + plain-language explanations) — Design Spec

- **Date:** 2026-06-10
- **Status:** Approved (the user requested labelled axes/units + customer-friendly explanations)
- **Phase:** 5 — Milestone 5F (a readability polish on top of the completed reporting engine)
- **Depends on:** 5A–5E (the report engine, charts, context, template) in `main`
- **Enables:** reports a non-technical customer can read and understand

## 1. Context & goal

The report is delivered to an MSP's **customer**, who may not be technical. Today the timeline charts are
bare (a line with no axes/units) and the sections have no explanation of what the reader is looking at.
5F adds: **labelled X/Y axes with units** on the timeline charts, and a short **plain-language
explanation** under each section heading.

## 2. Design decisions (5F)

| Topic | Decision |
|-------|----------|
| Chart axes | Enhance `line_chart` to draw a **Y axis** (4–5 ticks 0→max, light gridlines, value labels) and an **X axis** (a readable subset of time labels), plus an **axis title with units** on each axis. |
| Per-chart units | Each timeline passes its own `y_label` + value formatter: Attacks → "Attempts" (integer); Web Activity → "DNS lookups" (integer); Data Usage → "Data per period" with **human byte** ticks (KB/MB/GB); Up/Down → Y ticks "Down"/"Up" (0/1); Applications/Web Filter (mock) → "Sessions"/"Requests". X axis = "Time". |
| Value formatter | `line_chart` takes an optional `y_format: Callable[[float], str]` (default = integers). Context passes `human_bytes` for Data Usage and a Down/Up mapper for availability. Keeps `charts.py` pure (no import of `context`). |
| Explanations | A short, non-technical `<p class="explain">` under each section `<h3>` (static English copy — the report is English per the project directive). One or two sentences: what it is + how to read the chart/tables. |
| Scope | Only the **timeline (`line_chart`)** charts get axes (they're the ones with a time X axis). `bar_chart` is unused on the data path — left as-is. No data/aggregation changes. |

## 3. Components

- **`charts.line_chart(points, *, width, height, y_label="", x_label="Time", y_format=None)`** — rewritten
  to render: left/bottom margins; a Y axis line + N ticks (gridline + formatted value label) from 0 to max;
  an X axis line + a thinned set of time labels; rotated/small axis titles (`y_label` rotated at the left,
  `x_label` centred at the bottom); then the data polyline + point markers. All emitted text escaped.
  Empty input → axes + a centred "No data" note. Deterministic + pure (the `y_format` callable is internal).
- **`context.py`** — each `line_chart(...)` call passes the appropriate `y_label` + `y_format`. Add an
  `explain: str` to each block dataclass (Attacks/WebActivity/Bandwidth/Status), OR render the static
  explanation strings directly in the template per section (simpler — chosen). A `human_bytes`-based
  formatter and a Down/Up formatter live in `context.py` and are passed to `line_chart`.
- **`mock_sections.py`** — the Applications/Web Filter mock timelines pass `y_label="Sessions"/"Requests"`.
- **`templates/report.html.j2` + `report.css`** — a `.explain` paragraph (muted, slightly smaller) under
  each section heading with the customer-friendly copy; chart sizing tweaks if needed for the taller axes.

## 4. Explanation copy (customer-friendly, English)
- **Attacks:** "Attempted intrusions your firewall's threat detection blocked during this period. The chart shows how many attempts occurred over time; the tables list the most frequent attack types, which of your devices were targeted, and where the attempts came from."
- **Web Activity:** "The websites and online services your network looked up. The chart shows lookup volume over time; the tables show the most-visited sites, the busiest devices, and the domains that were blocked."
- **Data Usage:** "How much data flowed through your firewall over time (incoming plus outgoing). The totals below summarise the whole period."
- **Up/Down Status:** "Whether this firewall was online and reachable over the period. 'Uptime' is the share of time it was online — higher is better."
- **Applications:** "Applications seen on your network, each with a simple risk rating — green (Low), blue (Guarded), orange (High). These figures are sample data until application monitoring is enabled."
- **Web Filter:** "Categories of web content requested from your network, each with a risk rating. These figures are sample data until content categorisation is enabled."

## 5. Security & safety
- `line_chart` still escapes every emitted text (axis labels, tick values, time labels). The new `y_format`
  callable is internal (never user-controlled). No data/secret surface change. Autoescape unaffected (the
  SVG is still built from escaped values and marked safe at render, as before).

## 6. Milestone 5F breakdown (for the plan)
1. **Enhance `charts.line_chart`** (axes + ticks + gridlines + units + `y_format`) + tests (axis lines, tick labels present, escaping, empty-state "No data", a bytes-formatted Y tick).
2. **Wire units + explanations**: per-chart `y_label`/`y_format` in `context.py` + `mock_sections.py`; the `.explain` paragraphs + CSS in the template; render tests assert the explanations + axis unit text appear; full PDF still valid.
3. **Tech debt + sample render.**

## 7. Definition of "Done" (5F)
- Every timeline chart shows labelled X (time) and Y (with units) axes + value ticks; bytes charts show
  KB/MB/GB ticks; the availability chart shows Down/Up.
- Each report section has a short plain-language explanation a non-technical customer can follow.
- All escaped/secret-safe; backend suite green; a rendered sample PDF confirms the look.

## 8. Non-goals
- Re-styling the whole report / a full design system; interactive charts; localisation of the report copy
  (English only for now).
