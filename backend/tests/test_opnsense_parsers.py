from tests.opn_fixtures import load


def test_fixtures_load():
    assert load("system_resources.json")["memory"]["total"] == "8462950400"
    assert load("traffic_interface.json")["interfaces"]["wan"]["link state"] == "2"
    assert load("firmware_status.json")["product"]["product_version"] == "26.1.9"
