"""Tests for the server-side report i18n layer."""
from __future__ import annotations

from app.services.reporting.i18n import (
    _EN,
    LANGUAGE_NAMES,
    REPORT_LOCALES,
    available_locales,
    is_rtl,
    report_text,
)

TRANSLATED = ["it", "es", "fr", "de", "pt", "nl", "ru", "ar", "zh", "zh-TW", "ja"]
ALL_LOCALES = ["en", *TRANSLATED]


def test_en_attacks_title():
    assert report_text("en").attacks_title == "Attacks"


def test_available_locales_returns_all_twelve():
    codes = [c for c, _ in available_locales()]
    assert len(codes) == 12
    assert set(codes) == set(ALL_LOCALES)
    assert codes[0] == "en"  # en is always listed first
    assert {"zh-TW", "ar"} <= set(codes)


def test_new_locales_translate_attacks_title():
    """The 5 new locales must return their own attacks_title, not the en fallback."""
    assert report_text("ar").attacks_title == "الهجمات"
    assert report_text("ja").attacks_title == "攻撃"
    assert report_text("ru").attacks_title == "Атаки"
    assert report_text("zh").attacks_title == "攻击"
    assert report_text("zh-TW").attacks_title == "攻擊"
    # None of them silently fall back to en.
    for code in ("ar", "ja", "ru", "zh", "zh-TW"):
        assert report_text(code).attacks_title != "Attacks", f"{code} fell back to en"


def test_only_arabic_is_rtl():
    assert is_rtl("ar") is True
    for code in ("en", "ja", "zh", "zh-TW", "ru", "it"):
        assert is_rtl(code) is False, f"{code} should not be RTL"


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
