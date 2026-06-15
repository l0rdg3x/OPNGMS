"""Toggle-resolution tests for the report-enrichment section plumbing.

Precedence: BUILTIN_DEFAULTS < tenant settings.sections < device schedule.sections.
Unknown keys are ignored on every layer.
"""
from app.services.reporting.sections import (
    BUILTIN_DEFAULTS,
    SECTION_KEYS,
    resolve_sections,
)


def test_defaults_match_spec():
    # Client-friendly defaults from the spec.
    assert BUILTIN_DEFAULTS == {
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
    # Every default key is a known section key (and vice versa).
    assert set(BUILTIN_DEFAULTS) == set(SECTION_KEYS)


def test_resolve_with_no_overrides_returns_builtin_defaults():
    assert resolve_sections(None, None) == BUILTIN_DEFAULTS
    assert resolve_sections({}, {}) == BUILTIN_DEFAULTS


def test_tenant_settings_override_builtin():
    # health defaults OFF -> tenant turns it ON; summary defaults ON -> tenant turns it OFF.
    out = resolve_sections({"health": True, "summary": False}, None)
    assert out["health"] is True
    assert out["summary"] is False
    # untouched keys keep their builtin default
    assert out["attacks"] is True


def test_device_schedule_overrides_tenant_and_builtin():
    # tenant turns health ON; device schedule turns it back OFF -> schedule wins.
    out = resolve_sections({"health": True}, {"health": False})
    assert out["health"] is False
    # device schedule overrides a builtin too
    out2 = resolve_sections(None, {"summary": False})
    assert out2["summary"] is False


def test_unknown_keys_are_ignored():
    out = resolve_sections({"bogus": True}, {"also_bogus": False, "health": True})
    assert "bogus" not in out
    assert "also_bogus" not in out
    assert out["health"] is True
    # result is exactly the known section keys
    assert set(out) == set(SECTION_KEYS)


def test_result_always_covers_all_section_keys():
    out = resolve_sections({}, {})
    assert set(out) == set(SECTION_KEYS)
