from app.services.capability import build_inventory

XML = (
    "<opnsense>"
    "<system><hostname>fw1</hostname></system>"
    "<interfaces>"
    "<wan><if>igb0</if><descr>WAN</descr></wan>"
    "<lan><if>igb1</if><descr>LAN</descr></lan>"
    "</interfaces>"
    "<filter><rule><type>pass</type></rule></filter>"
    "</opnsense>"
)


def test_inventory_empirical_interfaces_and_sections():
    inv = build_inventory(XML, opnsense_version="24.7", plugin_info={"plugins": []})
    names = {i["name"] for i in inv["interfaces"]}
    assert names == {"wan", "lan"}
    wan = [i for i in inv["interfaces"] if i["name"] == "wan"][0]
    assert wan["nic"] == "igb0" and wan["description"] == "WAN"
    assert "system" in inv["configured_sections"]
    assert "interfaces" in inv["configured_sections"]
    assert "filter" in inv["configured_sections"]
    assert inv["opnsense_version"] == "24.7"


def test_inventory_maps_known_plugin_capabilities():
    inv = build_inventory(XML, opnsense_version="24.7", plugin_info={"plugins": ["os-wireguard"]})
    ids = {c["id"] for c in inv["available_capabilities"]}
    assert "os-wireguard" in ids
    wg = [c for c in inv["available_capabilities"] if c["id"] == "os-wireguard"][0]
    assert wg["label"]  # known plugin has a friendly label


def test_inventory_unknown_plugin_passes_through_generic():
    inv = build_inventory(XML, opnsense_version="24.7", plugin_info={"plugins": ["os-weird-thing"]})
    weird = [c for c in inv["available_capabilities"] if c["id"] == "os-weird-thing"][0]
    assert weird["label"]  # generic descriptor, not crash
