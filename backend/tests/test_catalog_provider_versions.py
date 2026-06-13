from app.services.catalog_provider import previous_version


def test_previous_version_picks_highest_strictly_below():
    versions = ["26.1", "26.1.1", "26.1.8", "26.1.9"]
    assert previous_version(versions, "26.1.9") == "26.1.8"
    assert previous_version(versions, "26.1.1") == "26.1"


def test_previous_version_none_when_lowest_or_unknown():
    versions = ["26.1", "26.1.9"]
    assert previous_version(versions, "26.1") is None
    assert previous_version(versions, "25.7") is None  # nothing strictly below
    assert previous_version([], "26.1.9") is None
