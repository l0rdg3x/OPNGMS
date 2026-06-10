from tests.opn_fixtures import load

from app.connectors.opnsense import parsers


def test_fixtures_load():
    assert load("system_resources.json")["memory"]["total"] == "8462950400"
    assert load("traffic_interface.json")["interfaces"]["wan"]["link state"] == "2"
    assert load("firmware_status.json")["product"]["product_version"] == "26.1.9"


def test_num_handles_units_and_tilde():
    assert parsers.num("12.3 ms") == 12.3
    assert parsers.num("0.0 %") == 0.0
    assert parsers.num("~") == 0.0
    assert parsers.num(5) == 5.0
    assert parsers.num(None) == 0.0


def test_parse_uptime():
    assert parsers.parse_uptime("00:11:14") == 674
    assert parsers.parse_uptime("2 days, 03:00:01") == 2 * 86400 + 3 * 3600 + 1
    assert parsers.parse_uptime("") == 0


def test_parse_cores():
    assert parsers.parse_cores(["Intel(R) ... (2 cores, 4 threads)"]) == 2
    assert parsers.parse_cores([]) == 1


def test_parse_system_info_against_real_fixtures():
    info = parsers.parse_system_info(
        load("system_resources.json"),
        load("system_disk.json"),
        load("system_time.json"),
        load("cpu_type.json"),
    )
    assert info["mem_pct"] == 8.9        # 755341425 / 8462950400 * 100
    assert info["disk_pct"] == 1.0       # used_pct of mountpoint "/"
    assert info["uptime_seconds"] == 674  # 00:11:14
    assert info["cpu_pct"] == 6.0        # load1m 0.12 / 2 cores * 100
