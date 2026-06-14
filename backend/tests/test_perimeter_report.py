"""The perimeter report sections (failed_logins / firewall_blocks) are standard report sections —
toggled with all the others via report_settings.sections / report_schedule.sections."""
from app.services.reporting.sections import BUILTIN_DEFAULTS, SECTION_KEYS, resolve_sections


def test_perimeter_sections_are_registered_and_default_on():
    for key in ("failed_logins", "firewall_blocks"):
        assert key in SECTION_KEYS
        assert BUILTIN_DEFAULTS[key] is True


def test_perimeter_sections_resolve_like_the_others():
    # default (nothing set) -> on
    eff = resolve_sections(None, None)
    assert eff["failed_logins"] is True and eff["firewall_blocks"] is True
    # tenant settings can turn one off
    eff = resolve_sections({"failed_logins": False}, None)
    assert eff["failed_logins"] is False and eff["firewall_blocks"] is True
    # a per-schedule (per-device) override wins over the tenant default
    eff = resolve_sections({"firewall_blocks": True}, {"firewall_blocks": False})
    assert eff["firewall_blocks"] is False
