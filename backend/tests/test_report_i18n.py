"""Tests for the server-side report i18n layer."""
from __future__ import annotations

import pytest

from app.services.reporting.i18n import REPORT_LOCALES, report_text


def test_en_attacks_title():
    assert report_text("en").attacks_title == "Attacks"


def test_unknown_locale_falls_back_to_en():
    t = report_text("xx")
    assert t.no_data == "No data"
    assert t.attacks_title == "Attacks"


def test_dict_access_threat_high():
    assert report_text("en")["threat_high"] == "High"


def test_partial_locale_overrides_one_key_falls_back_for_rest():
    """A partial locale dict overrides only its declared keys; all others fall back to en."""
    fake_locale = {"attacks_title": "Angriffe"}  # only one key overridden
    REPORT_LOCALES["de_test"] = fake_locale
    try:
        t = report_text("de_test")
        assert t.attacks_title == "Angriffe"          # overridden
        assert t.no_data == "No data"                 # fell back to en
        assert t.web_title == "Web Activity"          # fell back to en
        assert t["threat_low"] == "Low"               # fell back to en via dict access
    finally:
        del REPORT_LOCALES["de_test"]                 # restore REPORT_LOCALES
