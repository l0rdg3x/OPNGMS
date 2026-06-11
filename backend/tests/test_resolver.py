from app.connectors.opnsense.profiles import EndpointSpec, ProfileRule, Request
from app.connectors.opnsense.resolver import CapabilityResolver


def _spec(tag):
    # a marker spec we can identify by its single request path
    return EndpointSpec(requests=(Request("GET", tag),), combine=lambda r: r)


def _rules():
    return {
        "cap": [
            ProfileRule("business", None, None, _spec("biz")),
            ProfileRule("any", None, (20, 1, 0, 0), _spec("legacy")),
            ProfileRule("any", (24, 7, 0, 0), None, _spec("modern")),
            ProfileRule("any", None, None, _spec("default")),
        ],
    }


def _path(resolver, cap):
    return resolver.resolve(cap).requests[0].path


def test_edition_takes_priority():
    r = CapabilityResolver("business", "26.1.9", rules=_rules())
    assert _path(r, "cap") == "biz"


def test_legacy_below_max():
    r = CapabilityResolver("community", "18.7.1", rules=_rules())
    assert _path(r, "cap") == "legacy"


def test_modern_at_or_above_min():
    r = CapabilityResolver("community", "24.7.0", rules=_rules())   # inclusive min
    assert _path(r, "cap") == "modern"


def test_hotfix_boundary():
    # max is exclusive (20,1,0,0); 20.1.0 is NOT legacy, falls through to default
    r = CapabilityResolver("community", "20.1.0", rules=_rules())
    assert _path(r, "cap") == "default"
    # but 20.0.9_9 is still below the bound -> legacy
    r2 = CapabilityResolver("community", "20.0.9_9", rules=_rules())
    assert _path(r2, "cap") == "legacy"


def test_unknown_version_uses_newest():
    # empty/garbage version -> NEWEST sentinel -> never matches a bounded-max rule
    r = CapabilityResolver("community", "", rules=_rules())
    assert _path(r, "cap") == "modern"   # min (24,7) satisfied by NEWEST, before default


def test_resolve_never_returns_none():
    r = CapabilityResolver("community", "1.0", rules={"cap": [
        ProfileRule("any", None, None, _spec("only"))]})
    assert _path(r, "cap") == "only"


def test_resolve_unknown_capability_raises_clear_error():
    import pytest
    r = CapabilityResolver("community", "26.1.9", rules=_rules())
    with pytest.raises(ValueError):
        r.resolve("does-not-exist")
