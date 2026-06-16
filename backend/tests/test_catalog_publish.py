import hashlib

from tools.opnsense_catalog.publish import build_manifest, parse_business_base, sha256_hex


def test_sha256_hex_matches_hashlib():
    data = b'{"models": {}}'
    assert sha256_hex(data) == hashlib.sha256(data).hexdigest()


def test_build_manifest_maps_edition_version_to_sha():
    a = b'{"version": "26.1.7"}'
    b = b'{"version": "26.1.8"}'
    manifest = build_manifest({"community/26.1.7": a, "community/26.1.8": b})
    assert manifest == {
        "catalogs": {
            "community/26.1.7": hashlib.sha256(a).hexdigest(),
            "community/26.1.8": hashlib.sha256(b).hexdigest(),
        }
    }


_BE_26_4 = """
<html><body>
<h1>OPNsense 26.4 Business Edition</h1>
<p>This business release is based on the OPNsense 26.1.6 community version with
additional reliability improvements.</p>
</body></html>
"""

_BE_25_10 = "blah ... based on the OPNsense 25.7.9 community version ... blah"


def test_parse_business_base_extracts_community_base():
    out = parse_business_base({"26.4": _BE_26_4, "25.10": _BE_25_10})
    assert out == {"map": {"26.4": "26.1.6", "25.10": "25.7.9"}}


def test_parse_business_base_skips_pages_without_the_marker():
    out = parse_business_base({"26.4": _BE_26_4, "99.9": "<html>no marker here</html>"})
    assert out == {"map": {"26.4": "26.1.6"}}


# A Business hotfix is "based on the OPNsense X.Y.Z BUSINESS version" (the prior BE release), not a
# Community version — it must be followed transitively to the underlying Community base.
_BE_25_4_2 = "This business release is based on the OPNsense 25.1.12 community version."
_BE_25_4_3 = "This business release is based on the OPNsense 25.4.2 business version with fixes."


def test_parse_business_base_follows_business_chain_to_community():
    out = parse_business_base({"25.4.2": _BE_25_4_2, "25.4.3": _BE_25_4_3})
    # 25.4.3 -> (business) 25.4.2 -> (community) 25.1.12
    assert out == {"map": {"25.4.2": "25.1.12", "25.4.3": "25.1.12"}}


def test_parse_business_base_follows_multi_hop_business_chain():
    pages = {
        "25.4": "based on the OPNsense 25.1.4 community version",
        "25.4.1": "based on the OPNsense 25.4 business version",
        "25.4.2": "based on the OPNsense 25.4.1 business version",
    }
    out = parse_business_base(pages)
    assert out == {"map": {"25.4": "25.1.4", "25.4.1": "25.1.4", "25.4.2": "25.1.4"}}


def test_parse_business_base_skips_unresolvable_business_chain():
    # 25.4.3 chains onto 25.4.2, which is absent from `pages` -> cannot resolve -> dropped (never guess).
    out = parse_business_base({"25.4.3": _BE_25_4_3})
    assert out == {"map": {}}


def test_parse_business_base_breaks_business_cycle():
    pages = {
        "25.4.1": "based on the OPNsense 25.4.2 business version",
        "25.4.2": "based on the OPNsense 25.4.1 business version",
    }
    # A business->business cycle (no community anchor) terminates and yields no entry, not a hang.
    assert parse_business_base(pages) == {"map": {}}


from tools.opnsense_catalog.publish import release_versions


def test_release_versions_filters_and_sorts():
    tags = ["26.1.8", "junk", "stable/26.1", "25.7.9", "26.1.7", "v1.0", "26.1"]
    assert release_versions(tags) == ["25.7.9", "26.1", "26.1.7", "26.1.8"]


def test_release_versions_minimum_drops_older():
    tags = ["25.7.9", "26.1.7", "26.1.8"]
    assert release_versions(tags, minimum="26.1") == ["26.1.7", "26.1.8"]


def test_release_versions_dedupes():
    assert release_versions(["26.1.8", "26.1.8"]) == ["26.1.8"]
