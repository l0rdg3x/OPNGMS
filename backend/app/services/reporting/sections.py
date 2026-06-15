"""Report section toggle model.

A single JSONB-backed source of truth lets us add sections without a migration:
- ``report_settings.sections`` is the tenant-level default toggle map.
- ``report_schedule.sections`` is an optional per-schedule (so per-device) override.

Resolution at generation time layers them over the built-in defaults; unknown keys
on any layer are ignored so a stale/forward-compat map can never inject a phantom
section.
"""

from __future__ import annotations

# All known section keys (new + existing). Order here is informational only.
SECTION_KEYS: tuple[str, ...] = (
    "summary",
    "health",
    "alerts_wan",
    "firmware_config",
    "attacks",
    "attacker_countries",
    "failed_logins",
    "firewall_blocks",
    "reliability",
    "web",
    "data",
    "status",
    "applications",
    "web_filter",
)

# Client-friendly defaults: lead with reassuring/value sections, hide deep-technical
# and still-sample sections (health / applications / web_filter) by default.
BUILTIN_DEFAULTS: dict[str, bool] = {
    "summary": True,
    "health": False,
    "alerts_wan": True,
    "firmware_config": True,
    "attacks": True,
    "attacker_countries": True,
    "failed_logins": True,
    "firewall_blocks": True,
    "reliability": True,
    "web": True,
    "data": True,
    "status": True,
    "applications": False,
    "web_filter": False,
}


def resolve_sections(
    settings_sections: dict[str, bool] | None,
    schedule_sections: dict[str, bool] | None,
) -> dict[str, bool]:
    """Resolve the effective ``{section_key: bool}`` map.

    Precedence (lowest to highest): ``BUILTIN_DEFAULTS`` < tenant ``settings_sections``
    < device ``schedule_sections``. Keys not in :data:`SECTION_KEYS` are ignored.
    """
    merged = {**BUILTIN_DEFAULTS, **(settings_sections or {}), **(schedule_sections or {})}
    return {key: bool(merged[key]) for key in SECTION_KEYS}
