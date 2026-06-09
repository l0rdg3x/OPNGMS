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
