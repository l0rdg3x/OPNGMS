"""Tests for the server-side report i18n layer."""
from __future__ import annotations

import pytest

from app.services.reporting.i18n import (
    LANGUAGE_NAMES,
    REPORT_LOCALES,
    _EN,
    available_locales,
    report_text,
)

TRANSLATED = ["it", "es", "fr", "de", "pt", "nl"]


def test_en_attacks_title():
    assert report_text("en").attacks_title == "Attacks"


def test_all_locales_have_exactly_the_en_key_set():
    """Every shipped locale must define the SAME keys as en — no missing or extra keys."""
    en_keys = set(_EN)
    for code, strings in REPORT_LOCALES.items():
        assert set(strings) == en_keys, f"locale {code} key set differs from en"


def test_translated_locales_are_present_and_named():
    for code in TRANSLATED:
        assert code in REPORT_LOCALES
        assert code in LANGUAGE_NAMES
    codes = {c for c, _ in available_locales()}
    assert {"en", *TRANSLATED} <= codes


def test_translated_locales_actually_differ_from_en():
    """A few representative keys must be translated (not accidentally copied from en)."""
    for code in TRANSLATED:
        t = report_text(code)
        assert t.attacks_title != "Attacks", f"{code} attacks_title not translated"
        assert t.no_data != "No data", f"{code} no_data not translated"
        assert t.threat != "Threat", f"{code} threat not translated"


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
