from app.services.catalog_provider import resolve_target, resolve_version

_MANIFEST = {"catalogs": {"community/26.1.6": "x", "community/26.1.7": "x", "community/26.1.8": "x"}}
_BIZ = {"map": {"26.4": "26.1.6", "25.10": "25.7.9"}}


def test_resolve_version_exact():
    assert resolve_version(["26.1.7", "26.1.8"], "26.1.8") == "26.1.8"


def test_resolve_version_floor():
    assert resolve_version(["26.1.6", "26.1.8"], "26.1.7") == "26.1.6"


def test_resolve_version_none_below():
    assert resolve_version(["26.1.6"], "26.1.5") is None


def test_resolve_version_tolerates_suffix():
    assert resolve_version(["26.1.8"], "26.1.8_4") == "26.1.8"


def test_resolve_target_community_passthrough():
    assert resolve_target(_MANIFEST, None, "community", "26.1.8") == ("community", "26.1.8")


def test_resolve_target_community_floor():
    assert resolve_target(_MANIFEST, None, "", "26.1.9") == ("community", "26.1.8")


def test_resolve_target_business_maps_to_community_base():
    # BE 26.4 -> CE 26.1.6 (exact in the manifest)
    assert resolve_target(_MANIFEST, _BIZ, "business", "26.4") == ("community", "26.1.6")


def test_resolve_target_business_unmapped_is_none():
    assert resolve_target(_MANIFEST, _BIZ, "business", "24.1") is None


def test_resolve_target_business_base_below_manifest_is_none():
    # BE maps to a Community base older than anything published.
    biz = {"map": {"24.4": "24.1.1"}}
    assert resolve_target(_MANIFEST, biz, "business", "24.4") is None
