from app.services.reporting.mock_sections import applications_block, web_filter_block

LEVELS = {"low", "guarded", "high"}


def test_applications_block_deterministic_and_per_device():
    a1 = applications_block("fw-edge")
    a2 = applications_block("fw-edge")
    other = applications_block("fw-branch")
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
    w1 = web_filter_block("fw-edge")
    w2 = web_filter_block("fw-edge")
    assert w1 == w2
    assert w1.sample is True
    assert all(r.level in LEVELS for r in w1.top_categories.rows)
    assert w1.top_sites.rows and w1.top_initiators.rows
