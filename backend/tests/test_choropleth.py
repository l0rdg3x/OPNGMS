"""Pure unit tests for the report world-map choropleth SVG generator (no DB, no I/O beyond import)."""
from app.services.reporting.choropleth import _BASE_HEX, choropleth_svg


def test_choropleth_with_data_colors_countries():
    svg = choropleth_svg({"RU": 48.0, "US": 31.0})
    assert svg.startswith("<svg")
    assert svg.count("<path") >= 2  # many country paths
    # At least one country shaded off the base color (a non-base fill).
    assert 'fill="#' in svg
    fills = {
        seg.split('"', 1)[0]
        for seg in svg.split('fill="#')[1:]
    }
    colored = {f for f in fills if f"#{f}" != _BASE_HEX}
    assert colored, "expected at least one colored (non-base) country fill"
    # The legend max label is rendered (max share = 48.0%).
    assert "48.0%" in svg
    assert svg.endswith("</svg>")


def test_choropleth_empty_renders_base_map():
    svg = choropleth_svg({})
    assert svg.startswith("<svg")
    assert "<path" in svg  # base map still drawn
    # No max share -> 0% legend label, every country at the base color.
    assert "0%" in svg
    assert f'fill="{_BASE_HEX}"' in svg
    assert svg.endswith("</svg>")


def test_choropleth_unknown_codes_ignored():
    # Sentinels / non-geometry codes don't appear in the geojson and must not raise.
    svg = choropleth_svg({"PRIVATE": 50.0, "ZZ": 10.0})
    assert svg.startswith("<svg")
    assert "<path" in svg
