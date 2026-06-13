"""Unit tests for the GeoIp reader + Babel country-name localization (no DB / network)."""
from pathlib import Path

import maxminddb
import pytest

from app.services.geoip import PRIVATE, GeoIp, localized_country_name

FIXTURE = Path(__file__).parent / "fixtures" / "geoip-test.mmdb"


@pytest.fixture
def geoip():
    reader = maxminddb.open_database(str(FIXTURE))
    g = GeoIp(reader)
    yield g
    g.close()


def test_known_public_ips_resolve_to_country(geoip):
    assert geoip.country("77.88.8.8") == "RU"
    assert geoip.country("8.8.8.8") == "US"
    assert geoip.country("133.11.11.11") == "JP"
    assert geoip.country("1.0.0.5") == "US"


def test_private_ips_collapse_to_sentinel(geoip):
    assert geoip.country("10.0.0.1") == PRIVATE
    assert geoip.country("192.168.1.1") == PRIVATE
    assert geoip.country("127.0.0.1") == PRIVATE      # loopback
    assert geoip.country("169.254.1.1") == PRIVATE    # link-local


def test_unparseable_or_missing_returns_none(geoip):
    assert geoip.country("not-an-ip") is None
    assert geoip.country("") is None
    # A globally-routable public IP that is simply not in the tiny fixture db -> None (UNKNOWN).
    assert geoip.country("45.33.32.156") is None


def test_localized_country_name_per_locale():
    assert "Russia" in localized_country_name("RU", "en")
    assert "Russie" in localized_country_name("RU", "fr")
    assert "Russland" in localized_country_name("RU", "de")
    assert "روسيا" in localized_country_name("RU", "ar")
    assert "ロシア" in localized_country_name("RU", "ja")
    assert "俄罗斯" in localized_country_name("RU", "zh")        # Simplified


def test_zh_tw_maps_to_traditional():
    # zh-TW must resolve to Traditional Chinese (俄羅斯), distinct from Simplified (俄罗斯).
    assert localized_country_name("RU", "zh-TW") == "俄羅斯"
    assert localized_country_name("RU", "zh") == "俄罗斯"
    assert localized_country_name("RU", "zh-TW") != localized_country_name("RU", "zh")


def test_unknown_code_falls_back_to_the_code():
    # An ISO code with no CLDR name (and a garbage locale) must degrade to the code itself.
    assert localized_country_name("XX", "en") == "XX"
    assert localized_country_name("RU", "not-a-locale") == "RU"
