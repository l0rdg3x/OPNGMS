import hashlib

from tools.opnsense_catalog.publish import build_manifest, sha256_hex


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
