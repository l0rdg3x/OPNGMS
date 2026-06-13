from pathlib import Path

from tools.opnsense_catalog.discover import discover_plugin_models, parse_plugin_makefile


def test_parse_plugin_makefile_extracts_name_version_comment():
    text = (
        "PLUGIN_NAME=\t\thaproxy\n"
        "PLUGIN_VERSION=\t\t5.1\n"
        "PLUGIN_COMMENT=\t\tReliable, high performance TCP/HTTP load balancer\n"
        "PLUGIN_DEPENDS=\t\thaproxy\n"
    )
    meta = parse_plugin_makefile(text)
    assert meta == {
        "name": "haproxy",
        "version": "5.1",
        "comment": "Reliable, high performance TCP/HTTP load balancer",
    }


def test_parse_plugin_makefile_without_plugin_name_is_empty():
    # A framework Makefile (Mk/, Templates/) has no PLUGIN_NAME -> not a plugin.
    assert parse_plugin_makefile("CORE_NAME=\topnsense\nall:\n\techo hi\n") == {}


_MINI = Path(__file__).parent / "fixtures" / "opnsense_catalog" / "miniplugins"


def test_discover_plugin_models_pairs_each_model_with_its_plugin():
    found = discover_plugin_models(_MINI)
    by_pkg = {pms.plugin.package: pms for pms in found}
    assert set(by_pkg) == {"os-haproxy", "os-widget"}

    hap = by_pkg["os-haproxy"]
    assert hap.plugin.title == "Reliable, high performance TCP/HTTP load balancer"
    assert hap.plugin.category == "net"
    assert hap.plugin.version == "5.1"
    assert hap.source.module == "HAProxy"
    assert hap.source.model_xml.endswith("HAProxy/HAProxy.xml")

    # The framework Makefile under Mk/ contributes no plugin/model.
    assert all(pms.plugin.category != "Mk" for pms in found)
