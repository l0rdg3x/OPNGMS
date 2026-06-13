# GeoIP v2 — world map of attacker countries

**Date:** 2026-06-13 · **Status:** Approved direction (user: world choropleth, gradient by %, UI interactive + report). Builds on GeoIP v1 (#123).

## Goal

A **world choropleth map** of attacker countries, each country shaded by a **gradient on its share (%) of
attacks**, in **both** the per-tenant **Overview dashboard** (interactive) and the **PDF report** (static).
Complements — does not replace — the existing ranked "Top attacker countries" list.

## Data (already shipped in v1)
`attacker_countries` / `GET …/attacker-countries` return `[{code: ISO-3166-1 alpha-2, count, pct}]`, plus the
`PRIVATE`/`UNKNOWN` sentinels. The map keys on **alpha-2**. `PRIVATE`/`UNKNOWN` have no geography → **excluded
from the map** (still shown in the list).

## Shared decisions
- **Color scale (shared by both renderers so they match):** a single-hue sequential gradient on `pct` —
  neutral light gray for 0/absent → the brand teal→red ramp for higher %. Linear on pct, domain `[0, maxPct]`.
  Define the ramp once (a small list of stops) and use the same stops in the Python SVG and the JS map.
- Countries with no attacks render in the neutral base color.

## Assets (vendored — no runtime third-party fetch)
- **Frontend:** `react-simple-maps` (map) + `world-atlas` (`countries-110m.json` topojson, numeric ISO ids) +
  `topojson-client`. A small vendored **numeric→alpha2** lookup maps the topojson id to our `code`.
- **Backend:** a one-time-generated, simplified **world geojson keyed by alpha-2** (`backend/app/services/
  reporting/assets/world-countries.geo.json`), produced from the SAME world-atlas topojson via a committed
  Node script (`topojson-client` quantize→feature + numeric→alpha2). Committed so the report needs no build step.

## Backend (report)
- `app/services/reporting/choropleth.py` — `choropleth_svg(pct_by_code: dict[str,float], *, width=520,
  height=260) -> str`: load the vendored geojson, **equirectangular**-project each polygon to SVG coords, emit
  one `<path>` per country with `fill` from the shared ramp (pct 0/absent → base). Pure, deterministic, all
  text escaped (mirror `charts.py`). A small horizontal gradient **legend** (0%→max%).
- `context.py` — `AttackerCountriesBlock` gains a `map_svg: str` field; `build_context` builds it from the same
  `pct_by_code` it already computes for the section. Rendered above the ranked table in `report.html.j2`.

## Frontend (Overview)
- `src/overview/AttackerCountriesMap.tsx` — a `react-simple-maps` `ComposableMap`/`Geographies` over the
  vendored topojson; fill each country from the shared ramp by its pct (lookup via numeric→alpha2); **hover
  tooltip** = country name (`Intl.DisplayNames`) + count + pct; basic zoom/pan (`ZoomableGroup`). A gradient
  legend. Empty data → a muted "no attacks" state.
- Mount it in the Overview "Top attacker countries" card, above/beside the existing list. New i18n keys
  (`overview.attackerCountries.map*`) across 12 locales as needed.

## Testing
- Backend: `choropleth_svg` returns valid SVG containing a `<path>` for a seeded country with a non-base fill;
  empty input → a base map (no error); the report section embeds the map when `attacker_countries` is on.
- Frontend: the map renders, colors a country present in the data, shows the empty state on `[]`.
- The numeric→alpha2 lookup round-trips a few known countries (RU/US/JP…).

## Out of scope (v2)
- City/region granularity, animated/temporal maps, a separate full-screen map page. Per-device maps (the map
  is tenant-wide, like the list).
