import uuid

import pytest

from app.services import templates as tpl


def test_firewall_alias_is_registered_and_maps_to_alias():
    spec = tpl.TEMPLATE_KINDS["firewall_alias"]
    assert spec.change_kind == "alias"
    op, target, payload = spec.to_change({"name": "web", "type": "host", "content": ["1.2.3.4"]})
    assert op == "set" and target == "web" and payload["name"] == "web"
    assert spec.pinned == ("name", "type")


def test_validate_body_unknown_kind_raises():
    with pytest.raises(tpl.InvalidTemplateError):
        tpl.validate_body("nope", {})


def test_effective_body_uses_per_kind_pinned():
    # firewall_alias pins name+type; a patch cannot change them
    base = {"name": "web", "type": "host", "content": ["1.1.1.1"], "description": "b"}
    eff = tpl.effective_body("firewall_alias", base, {"name": "X", "type": "url", "content": ["2.2.2.2"]})
    assert eff["name"] == "web" and eff["type"] == "host" and eff["content"] == ["2.2.2.2"]


def test_register_and_materialize_a_custom_kind(monkeypatch):
    # Register a throwaway kind to prove extensibility, then clean up.
    def _validate(body):
        if not body.get("svc"):
            raise tpl.InvalidTemplateError("svc required")

    spec = tpl.TemplateKind(
        validate=_validate, change_kind="custom_demo",
        to_change=lambda body: ("set", body["svc"], body), pinned=("svc",),
    )
    tpl.register_template_kind("demo_kind", spec)
    try:
        assert "demo_kind" in tpl.TEMPLATE_KINDS
        tpl.validate_body("demo_kind", {"svc": "x"})
        with pytest.raises(tpl.InvalidTemplateError):
            tpl.validate_body("demo_kind", {})
        # effective_body pins "svc"
        eff = tpl.effective_body("demo_kind", {"svc": "a", "v": 1}, {"svc": "HACK", "v": 2})
        assert eff["svc"] == "a" and eff["v"] == 2
    finally:
        tpl.TEMPLATE_KINDS.pop("demo_kind", None)
