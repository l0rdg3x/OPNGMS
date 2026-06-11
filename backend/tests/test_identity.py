from app.connectors.opnsense.identity import DeviceIdentity, parse_identity
from tests.opn_fixtures import load


def test_parse_identity_community():
    ident = parse_identity(load("firmware_status.json"))
    assert ident == DeviceIdentity(edition="community", version="26.1.9", series="26.1")


def test_parse_identity_business():
    ident = parse_identity(load("firmware_status_business.json"))
    assert ident.edition == "business"
    assert ident.version == "24.10.2" and ident.series == "24.10"


def test_parse_identity_series_fallback_from_version():
    ident = parse_identity({"product": {"product_id": "opnsense", "product_version": "25.7.3_1"}})
    assert ident.series == "25.7" and ident.version == "25.7.3_1"


def test_parse_identity_defensive():
    assert parse_identity({}).edition == "community"
    assert parse_identity(None).version == ""


def test_parse_identity_never_raises_on_weird_shapes():
    assert parse_identity({"product": [1, 2]}).edition == "community"
    assert parse_identity({"product": {"product_id": 123}}).edition == "community"
    assert parse_identity("nonsense").edition == "community"


def test_parse_identity_product_id_is_primary_over_name():
    # A Community product_id must NOT be overridden by 'business' in product_name/repos.
    ident = parse_identity({"product": {
        "product_id": "opnsense", "product_name": "OPNsense Business Lite",
        "product_version": "26.1.0"}})
    assert ident.edition == "community"


def test_parse_identity_empty_series_when_no_version():
    assert parse_identity({}).series == ""
