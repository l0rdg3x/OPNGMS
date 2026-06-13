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
