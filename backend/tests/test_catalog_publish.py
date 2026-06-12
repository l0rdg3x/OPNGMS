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


from tools.opnsense_catalog.publish import release_versions


def test_release_versions_filters_and_sorts():
    tags = ["26.1.8", "junk", "stable/26.1", "25.7.9", "26.1.7", "v1.0", "26.1"]
    assert release_versions(tags) == ["25.7.9", "26.1", "26.1.7", "26.1.8"]


def test_release_versions_minimum_drops_older():
    tags = ["25.7.9", "26.1.7", "26.1.8"]
    assert release_versions(tags, minimum="26.1") == ["26.1.7", "26.1.8"]


def test_release_versions_dedupes():
    assert release_versions(["26.1.8", "26.1.8"]) == ["26.1.8"]
