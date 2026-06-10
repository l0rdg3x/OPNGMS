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
