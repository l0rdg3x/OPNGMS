# Drift detection (on-demand) for applied template changes — design

**Goal:** On demand, tell a tenant operator whether a device's **live** OPNsense config has drifted
from what OPNGMS **applied** from a template, and show a per-change badge in the device Config tab.

**Semantics (chosen):** drift = *applied value vs. live value*. For each **applied,
template-sourced** `ConfigChange` (latest per `(kind, target)`), re-read the live device and compare
the fields we set. This catches "someone changed it on the box after we pushed it." (Not
template-vs-live; that's a future increment.)

**Trigger (chosen):** on-demand — a "Check drift" button does a live probe now; no new persisted state.

**Surface (chosen):** badges next to the existing config changes in the Config tab.

**Coverage (chosen): all kinds, via an extensible registry.** Increment 1 ships the reliable
checkers — `opnsense_setting`, `firewall_alias`, `ids_rulesets`. `firewall_rule`/`monit_test` are
registered as **unsupported** for now (same blocker as their inverse builders: the config.xml↔API
field map for rules/tests is unverified on hardware — a wrong map yields false drift). The registry
makes adding them later a single checker function.

## Architecture

One live call set per probe: `get_config_backup()` (full config.xml) and, only if an `ids_rulesets`
change exists, `list_ids_rulesets()`. Per-kind comparison is done by **pure** checker functions
(inputs are data, not clients) so they're trivially unit-testable; the orchestrator does all I/O.

### Backend `app/services/config_drift.py`

```python
@dataclass(frozen=True)
class LiveState:
    config_xml: str
    ruleset_enabled: dict[str, bool]        # filename -> enabled (empty if not fetched)

@dataclass(frozen=True)
class DriftResult:
    change_id: uuid.UUID
    kind: str
    target: str
    status: str                             # "in_sync" | "drifted" | "missing" | "unsupported"
    drifted_fields: list[str]               # field NAMES only — never raw values (no secret leak)

# pure: (change, live) -> (status, drifted_fields)
DriftChecker = Callable[[ConfigChange, LiveState], tuple[str, list[str]]]
DRIFT_CHECKERS: dict[str, DriftChecker] = {}
register_drift_checker(kind, fn) / has_drift_checker(kind)
```

`_norm(v)`: list/tuple → `"\n".join(str(x))`; bool → `"1"/"0"`; else `str(v)`. Compares an applied
value to the XML text form.

- **`_check_opnsense_setting`**: `ep = SETTING_ENDPOINTS[endpoint_key]`; `prev =
  setting_from_config_xml(xml, ep.xml_path, changed_keys)`; field drifts where `_norm(applied[k]) !=
  prev[k]`. Unknown endpoint → `unsupported`.
- **`_check_alias`**: `cur = alias_from_config_xml(xml, name)`; `missing` if None; else compare each
  applied key except `name` (intersection-aware: a set field absent in XML counts as drift).
- **`_check_ids_rulesets`**: `payload["rulesets"]` should all be enabled live; drifted_fields =
  filenames not currently enabled (absent or `enabled` false). `missing` only if XML has no IDS node
  AND no ruleset info — practically: if a listed ruleset is absent from the catalog → drift on it.

`compute_drift(changes, live) -> list[DriftResult]`: dedupe to the latest applied change per
`(kind, target)` (input ordered created_at desc → first wins), dispatch each through its checker or
mark `unsupported`.

`needs_rulesets(changes) -> bool`: any selected change is `ids_rulesets`.

### Endpoint — `GET /api/tenants/{tid}/devices/{did}/config/drift-check`

(New name; the existing `config/drift` snapshot-count stub is unused by the UI but left intact.)
`require_tenant(Action.DEVICE_VIEW)`. Builds an `OpnsenseClient` from the device creds exactly like
`config_capabilities` (decrypt key/secret, `verify_tls`, `tls_fingerprint`). On any `OpnsenseError`
/ `InvalidToken` → `DriftReport(reachable=False, results=[], ...)` (graceful, like capabilities).

Selection: `ConfigChangeRepository.list(device_id)` → keep `status=="applied" and
source_template_id is not None`. No applied template change → empty report (`reachable=True`).

Response `DriftReport`:
```python
class DriftResultOut(BaseModel):
    change_id: uuid.UUID; kind: str; target: str; status: str; drifted_fields: list[str]
class DriftReport(BaseModel):
    reachable: bool; checked_at: datetime
    results: list[DriftResultOut]; unsupported_kinds: list[str]
```

### Frontend — Config tab badge

- `useDriftCheck(deviceId)` — a lazy query (enabled=false) over `GET …/config/drift-check`,
  triggered by a **Check drift** button in `ChangesPanel`/`ConfigTab`.
- Map `results` by `change_id`; render a badge per change row: `in_sync` (green), `drifted` (red, a
  tooltip listing `drifted_fields`), `missing` (orange), `unsupported` (gray). A banner when
  `reachable === false`. Re-fetch overwrites prior badges. openapi regen + typed hook.

## Security

- Tenant-scoped (`DEVICE_VIEW` + RLS); device loaded under the tenant session → no cross-tenant read.
- Live client reuses the audited `config_capabilities` path; no new secret handling.
- Response carries **field names + status only**, never raw config values → no secret leak (matches
  `structural_diff`'s paths-only convention).
- One `get_config_backup` (+ optional `list_ids_rulesets`) per call; on-demand only → bounded cost.
- Connector SSRF guard (`validate_base_url`) already protects the outbound probe.

## Testing

- Pure checkers (XML fixtures): setting drift / in-sync / unknown endpoint; alias drift / in-sync /
  missing; ids drift (one disabled) / in-sync.
- `compute_drift`: latest-per-target dedupe; unsupported kind → status `unsupported` +
  `unsupported_kinds`.
- Endpoint (RLS via `app_role_api_client`, fake client injected or monkeypatched): reachable report;
  unreachable → `reachable=False`; cross-tenant device → 404.
- Frontend: badges render per status; tooltip lists fields; unreachable banner. `npm run build` green.

## Out of scope (documented follow-ups)
- `firewall_rule` / `monit_test` checkers — need the hardware-verified config.xml↔API field map
  (same blocker as their inverse builders).
- Scheduled/cron drift with persisted state + proactive alerts (chosen trigger was on-demand).
- Template-vs-live semantics (drift from an *updated* template not yet re-applied).
