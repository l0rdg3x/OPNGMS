import pytest

from app.services import templates as tpl


def test_bind_default_is_identity():
    spec = tpl.TEMPLATE_KINDS["firewall_alias"]
    assert spec.bind is None  # alias has no bind -> identity


def test_register_kind_with_bind_and_effective_bind(monkeypatch):
    seen = {}

    def _validate(body):
        seen["validated"] = dict(body)

    spec = tpl.TemplateKind(
        validate=_validate, change_kind="x",
        to_change=lambda b: ("set", b.get("description", ""), b),
        pinned=("description",),
        bind=lambda body, b: {**body, "interface": b.get("interface", "")},
    )
    tpl.register_template_kind("_bindtest", spec)
    out = tpl.apply_bindings("_bindtest", {"description": "d"}, {"interface": "wan"})
    assert out == {"description": "d", "interface": "wan"}
    # no bindings -> floating (empty interface) via bind
    out2 = tpl.apply_bindings("_bindtest", {"description": "d"}, {})
    assert out2 == {"description": "d", "interface": ""}
    # a kind without bind returns the body unchanged
    out3 = tpl.apply_bindings("firewall_alias", {"name": "a", "type": "host", "content": ["1"]}, {"interface": "wan"})
    assert "interface" not in out3
