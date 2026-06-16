from app.connectors.opnsense.client import OpnsenseClient
from app.core import crypto
from app.models.device import Device
from app.services.device_client import client_for_device


def _device(**over):
    d = Device()
    d.base_url = "https://10.0.0.1"
    d.api_key_enc = crypto.encrypt("the-key")
    d.api_secret_enc = crypto.encrypt("the-secret")
    d.verify_tls = True
    d.tls_fingerprint = "AA:BB"
    d.edition = "community"
    d.firmware_series = "26.1"
    d.firmware_version = "26.1.10"
    for k, v in over.items():
        setattr(d, k, v)
    return d


def test_builds_client_with_decrypted_creds_and_tls():
    c = client_for_device(_device())
    assert isinstance(c, OpnsenseClient)
    assert c._base_url == "https://10.0.0.1"
    assert c._auth == ("the-key", "the-secret")      # Fernet round-trips through decrypt
    assert c._verify is True
    assert c._fingerprint == "AA:BB"


def test_passes_edition_and_version_to_the_resolver():
    c = client_for_device(_device())
    assert c._resolver.edition == "community"
    assert c._resolver.vtuple == (26, 1, 10, 0)      # full firmware_version "26.1.10" parsed


def test_unprobed_device_without_a_version_falls_back_to_newest():
    # firmware_version is nullable (not-yet-probed); the factory passes "" -> parse_version("") ->
    # (0,0,0,0) -> the resolver's _NEWEST, so the default profile is used (no crash on None).
    c = client_for_device(_device(firmware_version=None))
    assert c._resolver.vtuple == (9999, 99, 99, 99)         # _NEWEST


def test_verify_tls_false_and_no_fingerprint_are_honoured():
    c = client_for_device(_device(verify_tls=False, tls_fingerprint=None))
    assert c._verify is False and c._fingerprint is None


def test_equivalent_to_inline_construction_for_a_26x_device():
    # The edition/version wiring must NOT change endpoint resolution for the managed (26.1.x) fleet:
    # the version-sensitive capability (dns_events, legacy boundary at 20.1) resolves to the SAME default
    # spec whether or not edition/version were supplied. This is the behavior-preserving guarantee.
    d = _device()
    factory = client_for_device(d)
    inline = OpnsenseClient(
        d.base_url, crypto.decrypt(d.api_key_enc), crypto.decrypt(d.api_secret_enc),
        verify_tls=d.verify_tls, tls_fingerprint=d.tls_fingerprint,
    )
    assert factory._resolver.resolve("dns_events") is inline._resolver.resolve("dns_events")
