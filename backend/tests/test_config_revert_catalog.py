"""Unit tests for the catalog_setting inverse builder (pure: change + snapshot_xml -> inverse)."""
import uuid as _uuid

import pytest

from app.models.config_change import ConfigChange
from app.services.config_revert import NoInverseError, build_inverse, has_inverse

# A config.xml snapshot with the model's xml_path subtree: a prior scalar value and the
# pre-apply grid rows that the forward change modified (u1) / deleted (u2).
_SNAPSHOT = """<opnsense>
  <OPNsense>
    <Unbound>
      <general><x>old</x></general>
      <hosts>
        <host uuid="u1"><hostname>kept</hostname><server>10.0.0.1</server></host>
        <host uuid="u2"><hostname>gone</hostname><server>10.0.0.2</server></host>
      </hosts>
    </Unbound>
  </OPNsense>
</opnsense>"""

_ENDPOINTS = {"add": "unbound/settings/addHost", "set": "unbound/settings/setHost",
              "del": "unbound/settings/delHost"}


def _catalog_change(payload, result):
    c = ConfigChange()
    c.id = _uuid.uuid4()
    c.tenant_id = _uuid.uuid4()
    c.device_id = _uuid.uuid4()
    c.created_by = _uuid.uuid4()
    c.kind = "catalog_setting"
    c.operation = "set"
    c.target = payload["model_id"]
    c.payload = payload
    c.result = result
    c.status = "applied"
    return c


def _full_payload():
    return {
        "model_id": "unbound", "set_path": "unbound/settings/set",
        "reconfigure_path": "unbound/service/reconfigure", "model_root": "unbound",
        "xml_path": "OPNsense/Unbound",
        "scalars": {"general.x": "new"},
        "grids": [
            {"op": "add", "endpoints": _ENDPOINTS, "row": "host", "uuid": None,
             "item": {"hostname": "added", "server": "10.0.0.9"}},
            {"op": "set", "endpoints": _ENDPOINTS, "row": "host", "uuid": "u1",
             "item": {"hostname": "modified", "server": "10.0.0.1"}},
            {"op": "del", "endpoints": _ENDPOINTS, "row": "host", "uuid": "u2", "item": None},
        ],
    }


def _full_result():
    # index-aligned with payload["grids"]: the add op's live result carries the NEW uuid.
    return {"dry_run": False, "scalars": {"dry_run": False, "result": "set"}, "grids": [
        {"dry_run": False, "op": "add", "result": {"result": "saved", "uuid": "uNEW"}},
        {"dry_run": False, "op": "set", "result": {"result": "saved"}},
        {"dry_run": False, "op": "del", "result": {"result": "deleted"}},
    ]}


def test_has_inverse_catalog_setting():
    assert has_inverse("catalog_setting") is True


def test_inverse_restores_scalars_and_inverts_grids_in_payload_order():
    ch = _catalog_change(_full_payload(), _full_result())
    op, target, payload = build_inverse(ch, _SNAPSHOT)
    assert op == "set"
    assert target == "unbound"
    # paths carried through unchanged
    assert payload["set_path"] == "unbound/settings/set"
    assert payload["reconfigure_path"] == "unbound/service/reconfigure"
    assert payload["model_root"] == "unbound"
    assert payload["xml_path"] == "OPNsense/Unbound"
    # scalars restored to the snapshot's prior value
    assert payload["scalars"] == {"general.x": "old"}
    # each forward op (add, set, del) -> its inverse (del, set, add), in payload order; the ops target
    # distinct uuids so the order is immaterial to the applied result.
    grids = payload["grids"]
    assert [g["op"] for g in grids] == ["del", "set", "add"]
    # forward add -> inverse del of the NEW uuid (from change.result)
    assert grids[0] == {"op": "del", "endpoints": _ENDPOINTS, "row": "host",
                        "uuid": "uNEW", "item": None}
    # forward set u1 -> inverse set u1 with the PRIOR row fields
    assert grids[1]["op"] == "set"
    assert grids[1]["uuid"] == "u1"
    assert grids[1]["row"] == "host"
    assert grids[1]["endpoints"] == _ENDPOINTS
    assert grids[1]["item"] == {"hostname": "kept", "server": "10.0.0.1"}
    # forward del u2 -> inverse add of u2's prior row fields (no uuid)
    assert grids[2]["op"] == "add"
    assert grids[2]["uuid"] is None
    assert grids[2]["item"] == {"hostname": "gone", "server": "10.0.0.2"}


def test_add_with_no_live_result_is_skipped():
    # A dry-run add (or a missing/erroring result entry) added nothing: there is no uuid to delete,
    # so the inverse must SKIP it.
    payload = {
        "model_id": "unbound", "set_path": "unbound/settings/set",
        "reconfigure_path": "unbound/service/reconfigure", "model_root": "unbound",
        "xml_path": "OPNsense/Unbound", "scalars": {},
        "grids": [{"op": "add", "endpoints": _ENDPOINTS, "row": "host", "uuid": None,
                   "item": {"hostname": "added"}}],
    }
    result = {"dry_run": True, "scalars": None,
              "grids": [{"dry_run": True, "op": "add", "row": "host", "uuid": None}]}
    op, target, inv = build_inverse(_catalog_change(payload, result), _SNAPSHOT)
    assert op == "set"
    assert inv["grids"] == []  # nothing was really added -> nothing to undo


def test_pure_add_needs_no_snapshot():
    # A change with ONLY add ops can be inverted to dels using just change.result (no snapshot).
    payload = {
        "model_id": "unbound", "set_path": "unbound/settings/set",
        "reconfigure_path": "unbound/service/reconfigure", "model_root": "unbound",
        "xml_path": "OPNsense/Unbound", "scalars": {},
        "grids": [{"op": "add", "endpoints": _ENDPOINTS, "row": "host", "uuid": None,
                   "item": {"hostname": "a"}}],
    }
    result = {"dry_run": False, "scalars": None,
              "grids": [{"dry_run": False, "op": "add", "result": {"result": "saved", "uuid": "uX"}}]}
    op, target, inv = build_inverse(_catalog_change(payload, result), None)
    assert op == "set"
    assert inv["scalars"] == {}
    assert inv["grids"] == [{"op": "del", "endpoints": _ENDPOINTS, "row": "host",
                             "uuid": "uX", "item": None}]


def test_no_snapshot_with_scalars_raises():
    payload = {
        "model_id": "unbound", "set_path": "unbound/settings/set",
        "reconfigure_path": "unbound/service/reconfigure", "model_root": "unbound",
        "xml_path": "OPNsense/Unbound", "scalars": {"general.x": "new"}, "grids": [],
    }
    result = {"dry_run": False, "scalars": {"result": "set"}, "grids": []}
    with pytest.raises(NoInverseError):
        build_inverse(_catalog_change(payload, result), None)


def test_no_snapshot_with_del_grid_raises():
    payload = {
        "model_id": "unbound", "set_path": "unbound/settings/set",
        "reconfigure_path": "unbound/service/reconfigure", "model_root": "unbound",
        "xml_path": "OPNsense/Unbound", "scalars": {},
        "grids": [{"op": "del", "endpoints": _ENDPOINTS, "row": "host", "uuid": "u2", "item": None}],
    }
    result = {"dry_run": False, "scalars": None,
              "grids": [{"dry_run": False, "op": "del", "result": {"result": "deleted"}}]}
    with pytest.raises(NoInverseError):
        build_inverse(_catalog_change(payload, result), None)


def test_empty_xml_path_with_scalars_raises():
    # An older catalog change with no recorded xml_path can't locate prior state -> fail closed
    # (an empty path would otherwise read from the document root).
    payload = {
        "model_id": "unbound", "set_path": "unbound/settings/set",
        "reconfigure_path": "unbound/service/reconfigure", "model_root": "unbound",
        "xml_path": "", "scalars": {"general.x": "new"}, "grids": [],
    }
    result = {"dry_run": False, "scalars": {"result": "set"}, "grids": []}
    with pytest.raises(NoInverseError):
        build_inverse(_catalog_change(payload, result), _SNAPSHOT)
