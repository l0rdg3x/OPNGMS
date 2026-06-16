"""Server-side report localisation. Ships 12 locales (matching the frontend UI); the resolver falls
back to en for unknown/partial locales, so adding a language = adding a dict (no template surgery)."""

from app.services.reporting.locales import (
    ar,
    de,
    en,
    es,
    fr,
    it,
    ja,
    nl,
    pt,
    ru,
    zh,
    zh_tw,
)

# en is the key source and the fallback for any unknown/partial locale (see report_text).
_EN = en.STRINGS

REPORT_LOCALES: dict[str, dict[str, str]] = {
    "en": en.STRINGS,
    "it": it.STRINGS,
    "es": es.STRINGS,
    "fr": fr.STRINGS,
    "de": de.STRINGS,
    "pt": pt.STRINGS,
    "nl": nl.STRINGS,
    "ru": ru.STRINGS,
    "ar": ar.STRINGS,
    "zh": zh.STRINGS,
    "zh-TW": zh_tw.STRINGS,
    "ja": ja.STRINGS,
}

LANGUAGE_NAMES: dict[str, str] = {
    "en": "English", "it": "Italiano", "es": "Español", "fr": "Français",
    "de": "Deutsch", "pt": "Português", "nl": "Nederlands",
    "ru": "Русский", "ar": "العربية", "zh": "简体中文", "zh-TW": "繁體中文", "ja": "日本語",
}

# Right-to-left locales: the report template emits dir="rtl" (+ CSS direction) so WeasyPrint mirrors
# the layout and right-aligns text. Arabic is the only RTL language we ship today.
_RTL_LOCALES: frozenset[str] = frozenset({"ar"})


def is_rtl(locale: str) -> bool:
    """True for right-to-left locales (drives the report's dir="rtl")."""
    return locale in _RTL_LOCALES


def available_locales() -> list[tuple[str, str]]:
    # (code, display name) for every locale that has a dict, en first.
    codes = sorted(REPORT_LOCALES.keys(), key=lambda c: (c != "en", c))
    return [(c, LANGUAGE_NAMES.get(c, c)) for c in codes]


class ReportText:
    """Attribute/dict access to report strings (already merged with the en fallback)."""

    def __init__(self, strings: dict[str, str]) -> None:
        object.__setattr__(self, "_s", strings)

    def __getattr__(self, key: str) -> str:
        try:
            return object.__getattribute__(self, "_s")[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __getitem__(self, key: str) -> str:
        return object.__getattribute__(self, "_s")[key]


def report_text(locale: str = "en") -> ReportText:
    merged = {**_EN, **REPORT_LOCALES.get(locale, {})}  # unknown locale or partial -> en fallback
    return ReportText(merged)
