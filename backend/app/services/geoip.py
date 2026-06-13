"""GeoIP country resolution over a MaxMind/DB-IP mmdb reader, plus CLDR-localized country names.

`GeoIp` wraps a `maxminddb.Reader` and maps an IP -> ISO 3166-1 alpha-2 country code, collapsing
private/reserved space to the `PRIVATE` sentinel so internal scanners don't masquerade as a geography.
The reader is loaded once (process-cached by the provider) and used read-only per request — never any
outbound call. `localized_country_name` resolves a *real* ISO code to its name via Babel/CLDR for the
viewer's locale; the PRIVATE/UNKNOWN sentinels are NOT passed here (the caller substitutes i18n labels).
"""
from __future__ import annotations

import ipaddress
import logging

import maxminddb
from babel import Locale

logger = logging.getLogger(__name__)

#: Sentinel returned for private/reserved/loopback/link-local IPs (RFC1918, CGNAT, etc.).
PRIVATE = "PRIVATE"
#: Sentinel the aggregator uses when resolution returns None (unparseable / not found in the db).
UNKNOWN = "UNKNOWN"

# App-locale -> Babel-locale overrides. Only the two Chinese variants need a script suffix; the other
# ten app locales map directly to a Babel locale of the same name.
_BABEL_LOCALE = {"zh": "zh_Hans", "zh-TW": "zh_Hant"}


class GeoIp:
    """Thin wrapper over a `maxminddb.Reader` for ISO alpha-2 country lookups."""

    def __init__(self, reader: maxminddb.Reader) -> None:
        self._reader = reader

    def country(self, ip: str) -> str | None:
        """ISO 3166-1 alpha-2 code for `ip`, the `PRIVATE` sentinel, or None.

        Private/loopback/link-local/reserved/multicast addresses collapse to `PRIVATE` (not a country).
        Unparseable input or a db miss returns None (the caller rolls it up as `UNKNOWN`). Never raises.
        """
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return None
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
        ):
            return PRIVATE
        try:
            rec = self._reader.get(ip)
        except (ValueError, KeyError):
            return None
        if not isinstance(rec, dict):
            return None
        country = rec.get("country")
        if isinstance(country, dict):
            code = country.get("iso_code")
            if isinstance(code, str) and code:
                return code
        return None

    def close(self) -> None:
        self._reader.close()


def localized_country_name(code: str, locale: str) -> str:
    """CLDR territory name for an ISO alpha-2 `code` in the app `locale`; falls back to `code`.

    `locale` is an app locale (e.g. "en", "zh", "zh-TW"); only the two Chinese variants are remapped to
    a Babel script locale. The PRIVATE/UNKNOWN sentinels are NOT valid here — the caller substitutes the
    i18n labels. Any error (bad locale, missing CLDR data) degrades to returning the code unchanged.
    """
    babel_locale = _BABEL_LOCALE.get(locale, locale)
    try:
        return Locale.parse(babel_locale).territories.get(code, code)
    except Exception:  # name resolution must never break a render — degrade to the raw code
        logger.debug("country name resolution failed for %r/%r", code, locale, exc_info=True)
        return code
