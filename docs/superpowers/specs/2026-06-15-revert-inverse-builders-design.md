# Revert — Inverse Builders for the Remaining Config-Change Kinds — Design Spec

**Date:** 2026-06-15
**Status:** Approved design (the 3-kind scope was approved in brainstorming; `ids_policy` added as a 4th
after it shipped — snapshot-reconstructable, see §5). Writing the implementation plan next.
**Covers:** Extending the operator **Revert** (targeted-inverse of a config push) to the kinds it does not
yet support — `firewall_rule`, `monit_test`, `catalog_setting`, and `ids_policy` — completing the deferred
follow-up from the apply-pipeline-reliability milestone (`docs/superpowers/specs/2026-06-12-apply-pipeline-reliability-design.md`, §"Out of scope").

## Goal

Revert generates the **inverse of our own change** as a new `config_change` run through the existing apply
pipeline. Today `INVERSE_BUILDERS` (in `app/services/config_revert.py`) covers only `alias` and
`opnsense_setting`; the Revert button is disabled for every other kind. This milestone adds inverse
builders for the four remaining live-applied kinds so Revert works fleet-wide.

**No API or frontend change is needed.** `revertible` is computed as `status ∈ {applied,failed} AND
has_inverse(kind)` (`app/api/config.py:284`) and the Revert button auto-enables once a builder is
registered. The work is backend builders + two small connector `delete` ops.

## Background (current state, verified 2026-06-15)

- **Registry** (`app/services/config_revert.py`): `register_inverse_builder(kind, fn)`, `build_inverse`,
  `revert_change`. A builder is `(change: ConfigChange, snapshot_xml: str | None) -> (operation, target,
  payload)`. `revert_change` decrypts the pre-apply `config_snapshot` (Fernet+gzip) to `config.xml`,
  calls the builder, and `create_change(reverts_change_id=…)`. Snapshot-decrypt failure → `RevertError`
  (409). Existing builders: `_invert_alias`, `_invert_opnsense_setting` (the latter uses
  `setting_from_config_xml(xml, xml_path, dotted_keys)`).
- **Common shape:** the four target kinds all emit `operation="set"` from their `to_change`. The inverse
  is decided by the **pre-apply snapshot**: identity PRESENT in the snapshot → the change was an *update*
  → inverse = `set` with the prior fields; identity ABSENT → the change *created* it → inverse = `delete`;
  no snapshot → `NoInverseError` (Revert stays disabled, clear reason).
- **Connector deletes:** `apply_alias` already supports `delete`. `apply_ids_policy` already supports
  `delete` (`delPolicy/{uuid}`, built in v0.13.0). `apply_firewall_rule` and `apply_monit_test` are
  **upsert-only** today — they need a `delete` branch added. The catalog applier's grids already support
  `del` via `apply_grid_item`.

## Verified config.xml storage paths (real box 192.168.1.82, read-only `core/backup/download/this`)

| Kind | config.xml path | Identity | Field reconstruction |
|------|-----------------|----------|----------------------|
| `firewall_rule` | `OPNsense/Firewall/Filter/rules/rule/<rule uuid=…>` | `(description, interface)` | children are the portable field names (action, direction, ipprotocol, source_net, destination_net, source_port, destination_port, interface, description, …) — flat extraction |
| `monit_test` | `OPNsense/monit/test/<test uuid=…>` | `name` | children name, type, condition, action, path — flat extraction |
| `catalog_setting` | the model's `xml_path` (e.g. `OPNsense/Unbound`) | per scalar/grid | scalars: prior values at `xml_path`; grid rows: by uuid in the model subtree |
| `ids_policy` | `OPNsense/IDS/policies/policy/<policy uuid=…>` | `description` | children enabled, prio, action (comma str), rulesets (comma uuids), content (JSON), new_action; **rulesets uuids → filenames via `OPNsense/IDS/files/file` in the SAME snapshot** |

## Locked decisions

1. **Scope = `firewall_rule` + `monit_test` + `catalog_setting` + `ids_policy`.** `ids_rulesets` stays
   deferred (additive enable → needs a connector `disable` op + fuzzy pre-state reconstruction; low value;
   the button stays disabled with a reason). Firmware revert / full-config restore remain impossible.
2. **All four reconstruct purely from the pre-apply snapshot** (no box read during inverse building — the
   builders stay pure `(change, snapshot_xml) -> tuple`). For `ids_policy` the ruleset uuid→filename map is
   read from `OPNsense/IDS/files` in the same `config.xml`, so this holds.
3. **`set→set` restores the FULL prior record** (all child tags flat-extracted), not just the portable
   subset — so a revert faithfully restores fields the operator may have set outside the template. The
   per-kind apply is an idempotent upsert, so this converges even on a partially-applied source.
4. **`set` whose identity is ABSENT in the snapshot → `delete`** (the change created the record). Needs the
   connector delete op (added for firewall_rule/monit_test; already present for ids_policy).
5. **No snapshot (e.g. a dry-run-only `failed` source) → `NoInverseError`** (can't tell created-vs-modified;
   Revert disabled with the existing clear reason). Consistent with the alias/setting builders.
6. **Ships behind `LIVE_PUSH_ENABLED`** like all pushes; the inverse goes through the same staleness guard,
   advisory lock, audit, and master switch.

## Component design

### `app/services/config_revert.py` — four new builders + helpers

Helpers (mirroring `alias_from_config_xml` / `setting_from_config_xml`):
- `record_from_config_xml(xml, path, match: dict) -> dict | None` — find the element under `path` whose
  child tags match all `(tag, value)` in `match`; return `{child.tag: child.text or ""}` flat, or None.
  (Generalizes the alias-by-name extraction to `(description, interface)` and `name`/`description`.)

`_invert_firewall_rule(change, snapshot_xml)`:
- identity = `(description, interface)` from `change.target`/`change.payload`.
- no snapshot → `NoInverseError`.
- record present → `("set", description, <full prior fields>)`; absent → `("delete", description,
  {"description": …, "interface": …})`.

`_invert_monit_test(change, snapshot_xml)`:
- identity = `name`. record present → `("set", name, <full prior fields>)`; absent → `("delete", name,
  {"name": name})`; no snapshot → `NoInverseError`.

`_invert_ids_policy(change, snapshot_xml)`:
- identity = `description`. no snapshot → `NoInverseError`.
- record present → reconstruct the portable body: scalars (enabled, prio, new_action, description) direct;
  `action` = split the comma string to a list; `content` = `json.loads` (default `{}`); `rulesets` =
  split the comma uuids, then map each uuid→filename via a `{uuid: filename}` table built from
  `OPNsense/IDS/files/file` in the same snapshot (drop a uuid with no filename, with a logged note).
  → `("set", description, body)`.
- record absent → `("delete", description, {"description": description})` (connector already supports it).

`_invert_catalog_setting(change, snapshot_xml)`:
- no snapshot → `NoInverseError` if there are scalars or `del`/`set` grid ops (can't reconstruct);
  `add→del` needs only the result uuid.
- returns `("set", model_id, <inverted catalog payload>)` carrying the same `set_path`,
  `reconfigure_path`, `model_root`, plus inverted `scalars` + inverted `grids`:
  - **scalars** → prior values read from the snapshot at the model's `xml_path` (reuse
    `setting_from_config_xml`). **Requires adding `xml_path` to the catalog change payload** at proposal
    time (`app/api/catalog.py _build_payload` → include `model.get("xml_path")`).
  - **grid `del`** → inverse `add` with the row's prior fields (find the element with that uuid in the
    snapshot subtree under `xml_path`).
  - **grid `set`** → inverse `set` same uuid with the prior fields.
  - **grid `add`** → inverse `del` with the NEW uuid from `change.result["grids"][i]["result"]["uuid"]`
    (index-correlated with `payload["grids"]`; only ops that have a live result — tolerant of a partial
    `failed` source). Invert the grid op list in reverse order.

### Connector — two new `delete` branches (`app/connectors/opnsense/client.py`)

- `apply_firewall_rule`: add `operation == "delete"` → resolve uuid by `(description, interface)` via the
  existing `_resolve_rule_uuid`, `POST firewall/filter/delRule/{uuid}`, then `firewall/filter/apply`.
  dry-run mutates nothing. (Standard OPNsense endpoint.)
- `apply_monit_test`: add `operation == "delete"` → resolve uuid by `name` via `_resolve_monit_test_uuid`,
  `POST monit/settings/delTest/{uuid}`, then `monit/service/reconfigure`. **Limitation (documented):**
  delete does NOT detach the test from a Monit service first (rare; reconfigure tolerates a dangling ref).
- Both validate the box-sourced uuid before embedding (catchable `ApiError`, the v0.13.0 pattern), and run
  through the existing SSRF-guarded `_post`. **RUNTIME VERIFICATION** flag: `delRule`/`delTest` are new
  writes — the operator live-verifies on the box before relying on the create-revert path (set-restore
  reuses the already-verified setRule/setTest).

## Data model / API / Frontend

No schema change, no migration, **no API or frontend change** — Revert plumbing (the endpoint, the
`revertible` flag, the button, `reverts_change_id`) already exists from the apply-pipeline-reliability
milestone; registering the builders + adding `xml_path` to the catalog payload is the whole surface.

## Error handling

| Condition | Behaviour |
|-----------|-----------|
| No pre-apply snapshot | `NoInverseError` → Revert disabled / 409 with the existing reason |
| Snapshot can't be decrypted (MASTER_KEY rotated past retention) | existing `RevertError` → 409 |
| `set` source whose identity is absent in the snapshot | inverse = `delete` (needs the connector delete) |
| catalog `add→del` but the source was a dry-run (no result uuid) | skip that op (nothing was added) |
| Inverse apply itself fails/conflicts | a normal `failed`/`conflict` change, surfaced like any apply |
| Revert with `LIVE_PUSH_ENABLED` off | the inverse runs as a dry-run (preview), like every push |
| `ids_rulesets` (out of scope) | no builder → button stays disabled with a reason |

## Security

- No new authz surface: Revert stays `CONFIG_PUSH`-gated + CSRF + audited (`config.change.revert`),
  tenant+device scoped, behind the staleness guard and `LIVE_PUSH_ENABLED`. The inverse is just another
  `config_change`.
- The two new connector deletes go through the existing SSRF-guarded boundary; box-sourced uuids are
  charset-validated before URL embedding (the v0.13.0 `_OPN_UUID_RE` + `ApiError` pattern).
- Snapshot decryption reuses the existing Fernet path; the reconstructed payload is device config (rules /
  tests / policies / settings), not secrets.

## Testing

- **Builders (pure, no DB):** for each of the four kinds — `set→set` restore from a snapshot; created→
  `delete` when absent; missing-snapshot → `NoInverseError`; `has_inverse` true for the kind. `ids_policy`:
  rulesets uuid→filename mapping from the snapshot files table; `action` comma-split; `content` JSON-parse.
  `catalog_setting`: scalars restore; grid `del→add`, `set→set`, `add→del` (result uuid); partial-result
  tolerance; reverse order.
- **Connector deletes (fake transport):** `apply_firewall_rule("delete", …)` → `delRule/{uuid}` + apply,
  resolve-by-identity, dry-run no-op, absent → clean no-op; same for `apply_monit_test` → `delTest/{uuid}`.
- **Revert flow (mocked connector):** an applied change of each kind → `revert_change` builds the linked
  inverse → applies → device state reverted; failed/partial source converges; non-invertible (`ids_rulesets`)
  → button disabled / 4xx; RBAC + CSRF + tenant/device scoping unchanged.
- **Catalog payload:** `_build_payload` now carries `xml_path`; the existing catalog tests still pass.

## Build phases (informs the plan)

- **PR1 — `firewall_rule`**: `record_from_config_xml` helper + `_invert_firewall_rule` + connector `delRule`
  branch + tests.
- **PR2 — `monit_test`**: `_invert_monit_test` + connector `delTest` branch + tests.
- **PR3 — `ids_policy`**: `_invert_ids_policy` (incl. the files uuid→filename map) + tests. No connector
  change (delete already exists).
- **PR4 — `catalog_setting`**: add `xml_path` to the catalog payload + `_invert_catalog_setting`
  (scalars + grid inversion with result-uuid correlation) + tests. (Largest; last.)

## Out of scope / future

- `ids_rulesets` revert (additive enable; needs a connector `disable` + fuzzy pre-state). Button stays
  disabled with a reason.
- Detaching a Monit test from its service on delete-revert (rare; documented limitation).
- Firmware revert (no un-upgrade); full-config restore (no OPNsense restore API).
