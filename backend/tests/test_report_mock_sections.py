from app.services.reporting.i18n import report_text
from app.services.reporting.mock_sections import applications_block, web_filter_block

LEVELS = {"low", "guarded", "high"}


def test_applications_block_deterministic_and_per_device():
    t = report_text("en")
    a1 = applications_block("fw-edge", t)
    a2 = applications_block("fw-edge", t)
    other = applications_block("fw-branch", t)
    assert a1 == a2                       # deterministic for the same device name
    assert a1 != other                    # per-device distinct
    assert a1.sample is True
    assert a1.timeline_svg.startswith("<svg")
    for tbl in (a1.top_detected, a1.top_blocked, a1.top_categories):
        assert tbl.rows
        assert all(r.level in LEVELS for r in tbl.rows)
        assert all(r.count >= 1 for r in tbl.rows)
    assert a1.top_initiators.rows


def test_web_filter_block_deterministic_and_levels():
    t = report_text("en")
    w1 = web_filter_block("fw-edge", t)
    w2 = web_filter_block("fw-edge", t)
    assert w1 == w2
    assert w1.sample is True
    assert all(r.level in LEVELS for r in w1.top_categories.rows)
    assert w1.top_sites.rows and w1.top_initiators.rows


def test_applications_block_uses_i18n_labels():
    t = report_text("en")
    a = applications_block("fw-edge", t)
    assert a.top_detected.title == "Top Detected"
    assert a.top_detected.columns == ("Application", "Sessions")
    assert a.top_blocked.title == "Top Blocked"
    assert a.top_blocked.columns == ("Application", "Blocks")
    assert a.top_categories.title == "Top Categories"
    assert a.top_categories.columns == ("Category", "Sessions")
    assert a.top_initiators.title == "Top Initiators"
    assert a.top_initiators.columns == ("Initiator", "Sessions")


def test_web_filter_block_uses_i18n_labels():
    t = report_text("en")
    w = web_filter_block("fw-edge", t)
    assert w.top_categories.title == "Top Categories"
    assert w.top_categories.columns == ("Category", "Requests")
    assert w.top_sites.title == "Top Sites"
    assert w.top_sites.columns == ("Site", "Requests")
    assert w.top_initiators.title == "Top Initiators"
    assert w.top_initiators.columns == ("Initiator", "Requests")
