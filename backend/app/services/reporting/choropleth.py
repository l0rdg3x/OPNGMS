"""Pure function rendering a world choropleth as an SVG string. No runtime I/O, deterministic, escaped.

Mirrors charts.py: a single pure function returns an `<svg>` string built from vendored world geometry
(`assets/world-countries.geo.json`, feature `id` = ISO alpha-2). Countries are shaded by their share of
attacker traffic on a light ramp suited to the report's white background. The PRIVATE/UNKNOWN geoip
sentinels are not in the geometry, so they are naturally excluded.
"""
from __future__ import annotations

import json
import pathlib
from xml.sax.saxutils import escape

_GEOJSON_PATH = pathlib.Path(__file__).parent / "assets" / "world-countries.geo.json"

# Color ramp for the report's light background: base (absent / pct 0) -> hot (max share).
_BASE_HEX = "#e9edf2"
_HOT_HEX = "#d6336c"

# Loaded ONCE at import: a list of (alpha-2 code, [polygon-rings...]) where each ring is a list of
# [lon, lat] pairs. Polygon -> one ring group; MultiPolygon -> several. Equivalent to the GeoJSON
# geometry, flattened to a uniform list-of-polygons shape so the renderer doesn't branch on type.
def _load_features() -> list[tuple[str, list[list[list[float]]]]]:
    with _GEOJSON_PATH.open(encoding="utf-8") as fh:
        data = json.load(fh)
    features: list[tuple[str, list[list[list[float]]]]] = []
    for feat in data["features"]:
        code = feat["id"]
        geom = feat["geometry"]
        gtype = geom["type"]
        coords = geom["coordinates"]
        # Polygon -> wrap as a single-element list so both shapes are "list of polygons".
        polygons = [coords] if gtype == "Polygon" else coords
        # Each polygon is [outer_ring, hole_ring, ...]; we render every ring as a filled subpath.
        rings: list[list[list[float]]] = [ring for poly in polygons for ring in poly]
        features.append((code, rings))
    return features


_FEATURES = _load_features()


def _lerp_color(base_hex: str, hot_hex: str, frac: float) -> str:
    """Linear-interpolate between two #rrggbb colors; frac is clamped to [0, 1]. Returns #rrggbb."""
    frac = 0.0 if frac < 0 else 1.0 if frac > 1 else frac
    b = base_hex.lstrip("#")
    h = hot_hex.lstrip("#")
    br, bg, bb = int(b[0:2], 16), int(b[2:4], 16), int(b[4:6], 16)
    hr, hg, hb = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    r = round(br + (hr - br) * frac)
    g = round(bg + (hg - bg) * frac)
    bl = round(bb + (hb - bb) * frac)
    return f"#{r:02x}{g:02x}{bl:02x}"


def choropleth_svg(pct_by_code: dict[str, float], *, width: int = 520, height: int = 270) -> str:
    """A world choropleth shading each country by its attacker-share percentage.

    `pct_by_code` maps ISO alpha-2 code -> share %; absent / unknown codes render as the base color.
    Equirectangular projection (lon -180..180 -> 0..width, lat 90..-90 -> 0..map_h) with a 22px bottom
    margin reserved for a gradient legend. Deterministic and self-contained — the only input besides the
    data is the vendored geometry loaded at import. All text is escaped.
    """
    legend_h = 22
    map_h = height - legend_h

    max_pct = max(pct_by_code.values(), default=0)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" class="choropleth-svg">'
    ]

    def _x(lon: float) -> float:
        return (lon + 180) / 360 * width

    def _y(lat: float) -> float:
        return (90 - lat) / 180 * map_h

    for code, rings in _FEATURES:
        pct = pct_by_code.get(code, 0)
        frac = (pct / max_pct) if max_pct > 0 else 0.0
        fill = _lerp_color(_BASE_HEX, _HOT_HEX, frac)
        subpaths = []
        for ring in rings:
            if not ring:
                continue
            # Break the subpath wherever two consecutive points jump more than half the map width in x
            # (an antimeridian crossing, e.g. Russia's far east) — otherwise the equirectangular
            # projection draws a streak straight across the map connecting +180 back to -180.
            seg: list[str] = []
            prev_x: float | None = None
            for lon, lat in ring:
                x, y = _x(lon), _y(lat)
                if not seg:
                    seg.append(f"M{x:.1f},{y:.1f}")
                elif prev_x is not None and abs(x - prev_x) > width / 2:
                    seg.append(f"ZM{x:.1f},{y:.1f}")  # close the current subpath, start a fresh one
                else:
                    seg.append(f"L{x:.1f},{y:.1f}")
                prev_x = x
            seg.append("Z")
            subpaths.append("".join(seg))
        if not subpaths:
            continue
        d = "".join(subpaths)
        parts.append(
            f'<path d="{d}" fill="{fill}" stroke="#ffffff" stroke-width="0.3" />'
        )

    # Gradient legend in the reserved bottom strip. Drawn as N solid-color segments (not an SVG
    # <linearGradient>, which WeasyPrint does not render reliably) so the bar always shows the ramp.
    bar_x = 4.0
    bar_y = map_h + 6.0
    bar_w = 120.0
    bar_h = 8.0
    segments = 30
    seg_w = bar_w / segments
    for i in range(segments):
        color = _lerp_color(_BASE_HEX, _HOT_HEX, i / (segments - 1))
        parts.append(
            f'<rect x="{bar_x + i * seg_w:.2f}" y="{bar_y:.1f}" width="{seg_w + 0.5:.2f}" '
            f'height="{bar_h:.1f}" fill="{color}" />'
        )
    parts.append(
        f'<rect x="{bar_x:.1f}" y="{bar_y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" '
        f'fill="none" stroke="#cccccc" stroke-width="0.5" />'
    )
    parts.append(
        f'<text x="{bar_x:.1f}" y="{bar_y + bar_h + 8:.1f}" font-size="7" fill="#666" '
        f'text-anchor="start">{escape("0%")}</text>'
    )
    parts.append(
        f'<text x="{bar_x + bar_w:.1f}" y="{bar_y + bar_h + 8:.1f}" font-size="7" fill="#666" '
        f'text-anchor="end">{escape(f"{max_pct}%")}</text>'
    )

    parts.append("</svg>")
    return "".join(parts)
