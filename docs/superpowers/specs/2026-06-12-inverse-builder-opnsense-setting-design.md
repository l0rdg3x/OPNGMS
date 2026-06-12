# Inverse builder for `opnsense_setting` (config revert) — design

**Goal:** Make `opnsense_setting` config changes revertible, by registering an
`INVERSE_BUILDERS["opnsense_setting"]` that reconstructs the prior field values from the
pre-apply config.xml snapshot and emits a `set` inverse change.

**Context:** `config_revert.py` already reverts the `alias` kind. `revert_change()` reads the
pre-apply snapshot, calls `build_inverse(change, snapshot_xml)`, and creates the inverse as a
normal draft `config_change` linked via `reverts_change_id`. Today every non-`alias` kind raises
`NoInverseError` (Revert disabled in the UI). This adds the `opnsense_setting` kind. The other
curated kinds (`firewall_rule`, `monit_test`, `ids_rulesets`) are deferred — see *Out of scope*.

## Why `opnsense_setting` is the tractable next kind

- The `opnsense_setting` change is **always** `operation="set"` (the template `to_change` is
  `("set", body["endpoint_key"], body)`), and OPNsense's model `set` is a **partial merge** (only
  the templated dotted fields are written; nothing is clobbered). So the inverse is symmetric: a
  `set` of the **same** dotted fields back to their **previous** values.
- The previous values are recoverable from the **full config.xml** pre-apply snapshot (the same
  snapshot the `alias` revert already uses), at a deterministic XML location per endpoint.
- No new connector verb is required — the existing `apply_setting` `set` path applies the inverse.

## Change payload shape (input)

A `opnsense_setting` change has:

- `change.kind == "opnsense_setting"`
- `change.operation == "set"`
- `change.target == endpoint_key` (e.g. `"ids_general"`)
- `change.payload == {"endpoint_key": "ids_general", "payload": {"general.enabled": "1", "general.homenet": "10.0.0.0/8"}}`

The inner `payload` maps **dotted field paths** (relative to the endpoint's `model_root`) to string
values. Multi-select fields (`SettingEndpoint.multi_fields`) are comma-joined strings, stored the
same way in config.xml — so a direct text read round-trips correctly.

## XML location: add `xml_path` to `SettingEndpoint`

The API `model_root` (e.g. `"ids"`) is **not** the config.xml node name (the IDS plugin lives at
`OPNsense/IDS`). We add one field to the endpoint catalog:

```python
@dataclass(frozen=True)
class SettingEndpoint:
    ...
    xml_path: str = ""   # config.xml location of model_root, e.g. "OPNsense/IDS"
```

`ids_general` gets `xml_path="OPNsense/IDS"`. A dotted key `general.enabled` then resolves to the
config.xml element `OPNsense/IDS/general/enabled` (root element is `<opnsense>`; ElementTree
relative `find` from root handles this).

## Extraction: `setting_from_config_xml`

```python
def setting_from_config_xml(xml: str, xml_path: str, dotted_keys: Iterable[str]) -> dict:
    """Read prior values for `dotted_keys` from config.xml at `xml_path`.
    Missing element -> "" (revert clears the field, the safest default)."""
```

For each `k` in `dotted_keys`, find `xml_path + "/" + k.replace(".", "/")` from the document root
and return its text (or `""` if absent). Uses `defusedxml.ElementTree` like `alias_from_config_xml`.

## Inverse builder

```python
def _invert_opnsense_setting(change, snapshot_xml):
    endpoint_key = change.target or change.payload.get("endpoint_key", "")
    ep = SETTING_ENDPOINTS.get(endpoint_key)
    if ep is None:
        raise NoInverseError(f"unknown setting endpoint {endpoint_key!r}")
    if not snapshot_xml:
        raise NoInverseError("no pre-apply snapshot to reconstruct the setting from")
    changed = (change.payload or {}).get("payload", {})
    if not changed:
        raise NoInverseError("setting change has no fields to invert")
    prev = setting_from_config_xml(snapshot_xml, ep.xml_path, changed.keys())
    return "set", endpoint_key, {"endpoint_key": endpoint_key, "payload": prev}

register_inverse_builder("opnsense_setting", _invert_opnsense_setting)
```

`config_revert.py` imports `SETTING_ENDPOINTS` from the connector catalog. No import cycle:
`setting_endpoints.py` has no app imports.

## Error handling

- Unknown `endpoint_key` → `NoInverseError` (the catalog may have changed since the change ran).
- No snapshot → `NoInverseError` (a `set` cannot be reconstructed without prior values; matches the
  alias `set`/`delete` behavior).
- Empty inner `payload` → `NoInverseError` (nothing to revert).
- Missing field in the snapshot → reverts to `""` (clears it); not an error.

## Testing

Unit tests in `test_config_revert.py` (pure, no DB), mirroring the alias tests:

- `setting_from_config_xml` extracts nested values; missing → `""`.
- `set` inverts to `set` with the prior values from the snapshot (only the changed keys).
- unknown endpoint → `NoInverseError`; no snapshot → `NoInverseError`; empty payload → `NoInverseError`.
- `has_inverse("opnsense_setting") is True`.
- **Update** the two existing tests that used `"opnsense_setting"` as the stand-in for an
  unregistered kind (`test_has_inverse`, `test_unknown_kind_raises`) to use a still-unregistered
  kind string (e.g. `"firewall_rule"`).

## Out of scope (documented follow-ups)

These three requested kinds have real blockers and are deferred:

- **`firewall_rule`** / **`monit_test`**: the apply is an upsert; when it **added** a new resource
  the inverse must **delete** it, but the connector has **no** `delRule`/`delTest` verb yet. The
  `set`-on-existing case also needs config.xml → API field-name mapping (rule/test XML tags differ
  from API fields; unverified on hardware — risky to push live). Needs: connector delete verbs +
  a hardware-verified field map.
- **`ids_rulesets`**: purely additive (`toggleRuleset/<name>/1`); the pre-apply set of enabled
  rulesets is **not captured**, so there's nothing to toggle back to. Needs: snapshot the enabled
  ruleset list before apply.
