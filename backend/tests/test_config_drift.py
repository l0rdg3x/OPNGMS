import uuid

from app.models.config_change import ConfigChange
from app.services.config_drift import (
    LiveState,
    compute_drift,
    has_drift_checker,
    needs_rulesets,
)


def _change(kind, target, payload, *, status="applied", source_template=True, created_at=None):
    c = ConfigChange(tenant_id=uuid.uuid4(), device_id=uuid.uuid4(), created_by=uuid.uuid4(),
                     kind=kind, operation="set", target=target, payload=payload,
                     baseline_hash="", status=status)
    c.id = uuid.uuid4()
    c.source_template_id = uuid.uuid4() if source_template else None
    c.created_at = created_at
    return c


_IDS_XML = (
    "<opnsense><OPNsense><IDS version='1.0'><general>"
    "<enabled>0</enabled><homenet>192.168.0.0/16</homenet>"
    "</general></IDS></OPNsense></opnsense>"
)

_ALIAS_XML = (
    "<opnsense><OPNsense><Firewall><Alias><aliases>"
    "<alias uuid='u1'><enabled>1</enabled><name>WebServers</name><type>host</type>"
    "<content>10.0.0.1\n10.0.0.2</content><description>web</description></alias>"
    "</aliases></Alias></Firewall></OPNsense></opnsense>"
)


def _live(xml="<opnsense></opnsense>", rulesets=None):
    return LiveState(config_xml=xml, ruleset_enabled=rulesets or {})


# --- registry ---

def test_supported_kinds_registered():
    assert has_drift_checker("opnsense_setting") is True
    assert has_drift_checker("alias") is True
    assert has_drift_checker("ids_rulesets") is True
    # Deferred kinds are NOT registered -> reported as unsupported, never falsely "in sync".
    assert has_drift_checker("firewall_rule") is False
    assert has_drift_checker("monit_test") is False


# --- opnsense_setting ---

def test_setting_in_sync_when_live_matches_applied():
    change = _change("opnsense_setting", "ids_general",
                     {"endpoint_key": "ids_general", "payload": {"general.enabled": "0"}})
    [res] = compute_drift([change], _live(_IDS_XML))
    assert res.status == "in_sync"
    assert res.drifted_fields == []


def test_setting_drifted_reports_only_changed_field():
    change = _change("opnsense_setting", "ids_general",
                     {"endpoint_key": "ids_general",
                      "payload": {"general.enabled": "1", "general.homenet": "192.168.0.0/16"}})
    [res] = compute_drift([change], _live(_IDS_XML))
    assert res.status == "drifted"
    assert res.drifted_fields == ["general.enabled"]  # homenet still matches -> not listed


def test_setting_unknown_endpoint_is_unsupported():
    change = _change("opnsense_setting", "nope",
                     {"endpoint_key": "nope", "payload": {"a.b": "1"}})
    [res] = compute_drift([change], _live(_IDS_XML))
    assert res.status == "unsupported"


# --- alias ---

def test_alias_in_sync():
    change = _change("alias", "WebServers",
                     {"name": "WebServers", "type": "host", "content": ["10.0.0.1", "10.0.0.2"],
                      "description": "web"})
    [res] = compute_drift([change], _live(_ALIAS_XML))
    assert res.status == "in_sync"


def test_alias_drifted_on_content():
    change = _change("alias", "WebServers",
                     {"name": "WebServers", "type": "host", "content": ["10.0.0.9"]})
    [res] = compute_drift([change], _live(_ALIAS_XML))
    assert res.status == "drifted"
    assert res.drifted_fields == ["content"]


def test_alias_missing_when_absent_from_live():
    change = _change("alias", "Gone", {"name": "Gone", "type": "host", "content": ["1.1.1.1"]})
    [res] = compute_drift([change], _live(_ALIAS_XML))
    assert res.status == "missing"


def test_alias_delete_is_in_sync_when_absent_and_drifted_when_present():
    gone = _change("alias", "Gone", {"name": "Gone"})
    gone.operation = "delete"
    [res] = compute_drift([gone], _live(_ALIAS_XML))   # absent after a delete -> in sync, not missing
    assert res.status == "in_sync"
    back = _change("alias", "WebServers", {"name": "WebServers"})
    back.operation = "delete"
    [res2] = compute_drift([back], _live(_ALIAS_XML))   # reappeared after a delete -> drift
    assert res2.status == "drifted"


# --- ids_rulesets ---

def test_ids_in_sync_when_all_enabled():
    change = _change("ids_rulesets", "ids_rulesets", {"rulesets": ["et.rules", "abuse.rules"]})
    [res] = compute_drift([change], _live(rulesets={"et.rules": True, "abuse.rules": True}))
    assert res.status == "in_sync"


def test_ids_drifted_lists_disabled_rulesets():
    change = _change("ids_rulesets", "ids_rulesets", {"rulesets": ["et.rules", "abuse.rules"]})
    [res] = compute_drift([change], _live(rulesets={"et.rules": True, "abuse.rules": False}))
    assert res.status == "drifted"
    assert res.drifted_fields == ["abuse.rules"]


# --- compute_drift selection ---

def test_only_latest_applied_template_change_per_target_is_checked():
    import datetime as dt
    old = _change("opnsense_setting", "ids_general",
                  {"endpoint_key": "ids_general", "payload": {"general.enabled": "1"}},
                  created_at=dt.datetime(2026, 1, 1))
    new = _change("opnsense_setting", "ids_general",
                  {"endpoint_key": "ids_general", "payload": {"general.enabled": "0"}},
                  created_at=dt.datetime(2026, 2, 1))
    # Repository returns created_at desc -> newest first.
    results = compute_drift([new, old], _live(_IDS_XML))
    assert len(results) == 1
    assert results[0].status == "in_sync"  # newest applied "0" matches live "0"


def test_non_applied_and_non_template_changes_are_skipped():
    draft = _change("alias", "A", {"name": "A"}, status="draft")
    manual = _change("alias", "B", {"name": "B"}, source_template=False)
    assert compute_drift([draft, manual], _live(_ALIAS_XML)) == []


def test_unsupported_kind_surfaces_as_unsupported_status():
    change = _change("firewall_rule", "allow-web", {"description": "allow-web"})
    [res] = compute_drift([change], _live())
    assert res.status == "unsupported"
    assert res.drifted_fields == []


def test_needs_rulesets_true_only_with_ids_change():
    ids = _change("ids_rulesets", "ids_rulesets", {"rulesets": ["et.rules"]})
    alias = _change("alias", "A", {"name": "A"})
    assert needs_rulesets([ids]) is True
    assert needs_rulesets([alias]) is False
