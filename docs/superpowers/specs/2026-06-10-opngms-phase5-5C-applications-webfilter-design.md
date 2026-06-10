# OPNGMS — Phase 5 / Milestone 5C: Applications + Web Filter (mock) + Threat-Level — Design Spec

- **Date:** 2026-06-10
- **Status:** Approved (the user chose "full template, mock Applications" and to proceed through 5B–5E)
- **Phase:** 5 of 5 — Milestone 5C (the reference layout's remaining sections, with labeled mock data)
- **Depends on:** 5A (engine), 5B (real sections) — in `main`
- **Enables:** 5D (white-label config), 5E (scheduled + history)

## 1. Context

The reference MSP report has **Applications** and **Web Filter** sections that depend on **app-id /
flow visibility** and **content categorization** — neither is ingested yet (OPNsense would need a
NetFlow/app-id plugin and a category feed). The user chose to ship the **full template now with labeled
mock data** for these sections, plus the reference's **threat-level color coding**
(Low=green, Guarded=blue, High=orange). 5C adds those two sections as **clearly-marked sample data** so
the report looks complete without ever implying the numbers are real.

## 2. Design decisions (5C)

| Topic | Decision |
|-------|----------|
| Data source | **Deterministic mock** (no DB, no random) — a pure provider generates representative rows; **varies per device** (seeded by device name) so firewalls differ but output is stable/testable. |
| Honesty | Every mock section renders a visible **"Sample data — application/content visibility not yet ingested"** caption. Never presented as real. |
| Applications | Timeline + **Top Detected** + **Top Blocked** + **Top Categories** + **Top Initiators**, with a **Threat Level** badge per row (Low/Guarded/High). |
| Web Filter | **Top Categories** + **Top Sites** + **Top Initiators** + Timeline (mock categories). |
| Threat-level | A small `ThreatLevel` enum + CSS classes `.threat-low/.threat-guarded/.threat-high` (controlled enum → safe class names, not user data). |
| Real-data later | When app-id/category ingest lands, the mock provider is swapped for a real aggregator with the same block shape — no template change. |

## 3. Components

- **`app/services/reporting/mock_sections.py`** (new): pure, deterministic providers
  `applications_block(device_name) -> ApplicationsBlock`, `web_filter_block(device_name) -> WebFilterBlock`.
  Seeded by `hash(device_name)` into a fixed palette of plausible apps/categories/sites so output is
  stable and per-device-distinct. No I/O, no `random`/time. A timeline SVG via `line_chart` (numeric).
- **`context.py`**: new dataclasses `ThreatRow(label, count, level)`, `ThreatRankedTable(title, columns,
  rows: list[ThreatRow])`, `ApplicationsBlock(timeline_svg, top_detected, top_blocked, top_categories,
  top_initiators, sample=True)`, `WebFilterBlock(timeline_svg, top_categories, top_sites, top_initiators,
  sample=True)`. Extend `DeviceSection` with `applications`, `web_filter`. `build_context` populates them
  per device from the mock provider.
- **Template + CSS**: render the two sections (each with the sample caption + threat badges). Threat
  badge: `<span class="threat threat-{{ level }}">{{ level }}</span>` where `level ∈ {low,guarded,high}`
  (controlled). CSS classes color the badge. Ranked tables reuse the existing styles + a threat column.

## 4. Data flow & safety

- Mock provider → context blocks → template (autoescape ON). Mock strings are trusted constants but still
  autoescaped (defense-in-depth). Threat level is a controlled enum → safe CSS class. No DB, no secrets,
  no SSRF surface (inherited 5A `_blocked_fetcher`). Tenant-scoping is irrelevant for mock (no tenant
  data), but the section still renders per device within the tenant's report.

## 5. Milestone 5C breakdown (for the plan)
1. **Threat model + mock provider**: `ThreatLevel`/`ThreatRow`/`ThreatRankedTable` + `mock_sections.py`
   (deterministic, per-device); pure-function tests (stable output, per-device-distinct, valid levels).
2. **Context + template + CSS**: `ApplicationsBlock`/`WebFilterBlock`, wire into `build_context`, render
   the two sections with threat badges + the **sample-data caption**; render tests assert the sections,
   the threat classes, and the visible sample-data label appear; full PDF still valid.
3. **Technical debt** notes.

## 6. Definition of "Done" (5C)
- A generated report shows, per firewall, **Applications** (timeline + Top Detected/Blocked/Categories/
  Initiators with Low/Guarded/High threat badges) and **Web Filter** (Categories/Sites/Initiators/
  timeline), each clearly labeled **sample data**.
- Deterministic, per-device-distinct; autoescaped; threat colors via controlled CSS classes; PDF valid;
  backend suite green; no migration.

## 7. Non-goals (5C) / deferred
- **Real app-id / flow / content categorization** — needs ingest (a future phase); the mock provider is a
  drop-in placeholder with the same block shape.
- **White-label config** (5D), **scheduling/storage/history + UI** (5E).
