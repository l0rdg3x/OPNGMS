from app.connectors.opnsense import parsers


def test_parse_version_basic_and_hotfix():
    assert parsers.parse_version("26.1.9") == (26, 1, 9, 0)
    assert parsers.parse_version("26.1.9_1") == (26, 1, 9, 1)
    assert parsers.parse_version("24.7.1_2") == (24, 7, 1, 2)
    assert parsers.parse_version("26.1") == (26, 1, 0, 0)


def test_parse_version_ordering():
    assert parsers.parse_version("26.1.9_1") > parsers.parse_version("26.1.9")
    assert parsers.parse_version("26.1.9") > parsers.parse_version("26.1.8")
    assert parsers.parse_version("26.1.0") > parsers.parse_version("25.7.5_9")


def test_parse_version_defensive():
    assert parsers.parse_version("") == (0, 0, 0, 0)
    assert parsers.parse_version(None) == (0, 0, 0, 0)
    assert parsers.parse_version("garbage") == (0, 0, 0, 0)


def test_series_of():
    assert parsers.series_of("26.1.9_1") == "26.1"
    assert parsers.series_of("24.7") == "24.7"
    assert parsers.series_of("") == "0.0"
