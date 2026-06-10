"""Pure functions producing SVG strings from data. No I/O, deterministic, all text escaped."""
from __future__ import annotations

from collections.abc import Callable
from xml.sax.saxutils import escape

_PAD = 24

# Axis margins (left for Y labels, bottom for X labels, small top/right).
_ML, _MR, _MT, _MB = 48, 12, 12, 30


def _svg_open(width: int, height: int) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" class="chart-svg">'
    )


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
    empty_text: str = "No data",
) -> str:
    """A time-series line chart with labelled X/Y axes, value ticks and gridlines.

    `points` is [(x_label, value)] (x_labels are time buckets). `y_format` formats the Y tick values
    (e.g. human-readable bytes); it is an INTERNAL callable, never user-controlled. All text is escaped.
    `empty_text` is the centred message shown when `points` is empty (escaped before rendering).
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
            f'text-anchor="middle">{escape(empty_text)}</text></svg>'
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

    # X tick labels (thin to ~6 to avoid crowding; always label the last point). Anchor the first
    # label at its left and the last at its right so they don't bleed over the Y labels / past the
    # right edge; suppress a penultimate label that would collide with the last.
    max_labels = 6
    every = max(1, (n + max_labels - 1) // max_labels)
    for i, (lab, _v) in enumerate(points):
        if i % every != 0 and i != n - 1:
            continue
        if i != n - 1 and (n - 1 - i) < every:
            continue  # too close to the always-shown last label
        anchor = "start" if i == 0 else ("end" if i == n - 1 else "middle")
        x = x0 + i * step
        parts.append(
            f'<text x="{x:.1f}" y="{y0 + 12}" font-size="7" fill="#666" '
            f'text-anchor="{anchor}">{escape(lab)}</text>'
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
