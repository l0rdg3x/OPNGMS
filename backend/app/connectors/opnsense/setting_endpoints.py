"""Curated catalog of fleet-portable OPNsense model-setting endpoints that may be templated.

Only portable settings are listed (no inherently hardware/device-specific endpoints), and each
entry declares `exclude_fields` for any per-device fields to omit from the form. Adding an endpoint
is a data-only change."""
from dataclasses import dataclass


@dataclass(frozen=True)
class SettingEndpoint:
    key: str
    label: str
    get_path: str
    set_path: str
    reconfigure_path: str
    model_root: str
    multi_fields: tuple[str, ...] = ()       # dotted paths that are multi-select option fields
    exclude_fields: tuple[str, ...] = ()      # dotted paths to OMIT (hardware/device-specific)
    xml_path: str = ""                         # config.xml location of model_root (for revert), e.g. "OPNsense/IDS"


SETTING_ENDPOINTS: dict[str, SettingEndpoint] = {
    "ids_general": SettingEndpoint(
        key="ids_general", label="IDS — General settings",
        get_path="ids/settings/get", set_path="ids/settings/set",
        reconfigure_path="ids/service/reconfigure", model_root="ids",
        multi_fields=("general.homenet",),
        exclude_fields=("general.interfaces",),   # per-device hardware — not templatable
        xml_path="OPNsense/IDS",                  # the IDS model lives at OPNsense/IDS in config.xml
    ),
}
