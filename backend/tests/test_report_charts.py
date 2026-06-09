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
