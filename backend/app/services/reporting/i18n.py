"""Server-side report localisation. English is the only locale today; the resolver falls back to en,
so adding a language = adding a dict (no template surgery) — mirrors the frontend i18n maturity."""
from __future__ import annotations

_EN: dict[str, str] = {
    # section titles
    "attacks_title": "Attacks",
    "web_title": "Web Activity",
    "data_title": "Data Usage",
    "status_title": "Up/Down Status",
    "apps_title": "Applications",
    "webfilter_title": "Web Filter",
    "toc_title": "Table of contents",
    # explanations
    "attacks_explain": "Attempted intrusions your firewall's threat detection blocked during this period. The chart shows how many attempts occurred over time; the tables list the most frequent attack types, which of your devices were targeted, and where the attempts came from.",
    "web_explain": "The websites and online services your network looked up. The chart shows lookup volume over time; the tables show the most-visited sites, the busiest devices, and the domains that were blocked.",
    "data_explain": "How much data flowed through your firewall over time (incoming plus outgoing). The totals below summarise the whole period.",
    "status_explain": "Whether this firewall was online and reachable over the period. 'Uptime' is the share of time it was online — higher is better.",
    "apps_explain": "Applications seen on your network, each with a simple risk rating — green (Low), blue (Guarded), orange (High). These figures are sample data until application monitoring is enabled.",
    "webfilter_explain": "Categories of web content requested from your network, each with a risk rating. These figures are sample data until content categorisation is enabled.",
    "apps_sample": "Sample data — application visibility not yet ingested.",
    "webfilter_sample": "Sample data — content categorization not yet ingested.",
    # misc
    "no_data": "No data",
    "total_in": "Total in",
    "total_out": "Total out",
    "uptime": "Uptime",
    "threat": "Threat",
    "threat_low": "Low",
    "threat_guarded": "Guarded",
    "threat_high": "High",
    # ranked-table titles + columns
    "t_top_attempts": "Top Attempts",
    "t_top_targets": "Top Targets",
    "t_top_initiators": "Top Initiators",
    "t_top_sites": "Top Sites",
    "t_top_blocked": "Top Blocked",
    "t_top_detected": "Top Detected",
    "t_top_categories": "Top Categories",
    "col_signature": "Signature",
    "col_count": "Count",
    "col_target": "Target",
    "col_initiator": "Initiator",
    "col_site": "Site",
    "col_hits": "Hits",
    "col_domain": "Domain",
    "col_blocks": "Blocks",
    "col_application": "Application",
    "col_sessions": "Sessions",
    "col_category": "Category",
    "col_requests": "Requests",
    # axis labels
    "axis_time": "Time",
    "axis_attempts": "Attempts",
    "axis_dns": "DNS lookups",
    "axis_data": "Data / period",
    "axis_status": "Status",
    "axis_sessions": "Sessions",
    "axis_requests": "Requests",
    "status_up": "Up",
    "status_down": "Down",
    # footer labels
    "footer_tz": "Report generated for timezone",
    "footer_owner": "Report owner:",
    "footer_page": "Page",
    "footer_of": "/",
}

REPORT_LOCALES: dict[str, dict[str, str]] = {"en": _EN}

LANGUAGE_NAMES: dict[str, str] = {
    "en": "English", "it": "Italiano", "es": "Español", "fr": "Français",
    "de": "Deutsch", "pt": "Português", "nl": "Nederlands",
}


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
