import pytest

from app.services.templates import (
    InvalidTemplateError,
    effective_body,
    validate_alias_body,
)


def test_validate_alias_body_ok():
    body = {"name": "web", "type": "host", "content": ["1.2.3.4"], "description": "x"}
    validate_alias_body(body)  # no raise


@pytest.mark.parametrize("bad", [
    {"type": "host", "content": ["1.2.3.4"]},               # missing name
    {"name": "", "type": "host", "content": ["1.2.3.4"]},    # empty name
    {"name": "web", "type": "host", "content": []},          # empty content
    {"name": "web", "type": "bogus", "content": ["1.2.3.4"]},# bad type
    {"name": "web", "type": "host", "content": "1.2.3.4"},   # content not a list
])
def test_validate_alias_body_rejects(bad):
    with pytest.raises(InvalidTemplateError):
        validate_alias_body(bad)


def test_effective_body_merges_patch_but_pins_name_and_type():
    base = {"name": "web", "type": "host", "content": ["1.1.1.1"], "description": "base"}
    patch = {"content": ["2.2.2.2", "3.3.3.3"], "description": "cust", "name": "HACK", "type": "url"}
    eff = effective_body("firewall_alias", base, patch)
    assert eff["content"] == ["2.2.2.2", "3.3.3.3"]   # patched
    assert eff["description"] == "cust"               # patched
    assert eff["name"] == "web" and eff["type"] == "host"  # pinned to base
    validate_alias_body(eff)


def test_effective_body_no_patch_returns_base():
    base = {"name": "web", "type": "host", "content": ["1.1.1.1"], "description": "base"}
    assert effective_body("firewall_alias", base, {}) == base
