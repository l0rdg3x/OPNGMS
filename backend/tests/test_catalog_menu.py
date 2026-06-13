from tools.opnsense_catalog.menu import parse_menu

_FRAG = """
<menu>
  <Services>
    <Unbound VisibleName="Unbound DNS" cssClass="fa fa-tags fa-fw">
      <General order="10" url="/ui/unbound/general"/>
      <ACL VisibleName="Access Lists" order="40" url="/ui/unbound/acl"/>
    </Unbound>
  </Services>
</menu>
"""


def test_parse_menu_builds_nodes():
    nodes = parse_menu(_FRAG)
    assert len(nodes) == 1
    services = nodes[0]
    assert services["id"] == "Services" and services["label"] == "Services"
    unbound = services["children"][0]
    assert unbound["id"] == "Unbound" and unbound["label"] == "Unbound DNS"
    assert unbound["icon"] == "fa fa-tags fa-fw"
    general = unbound["children"][0]
    assert general["url"] == "/ui/unbound/general" and general["order"] == 10
    acl = unbound["children"][1]
    assert acl["label"] == "Access Lists" and "children" not in acl  # leaf
from tools.opnsense_catalog.menu import merge_menus

_A = parse_menu("""
<menu><Services><Unbound VisibleName="Unbound DNS"><General order="10" url="/ui/unbound/general"/></Unbound></Services></menu>
""")
_B = parse_menu("""
<menu><Services><IDS VisibleName="Intrusion Detection"><Admin order="10" url="/ui/ids"/></IDS></Services></menu>
""")


def test_merge_unions_under_same_category_sorted():
    merged = merge_menus([_A, _B])
    assert [c["id"] for c in merged] == ["Services"]
    groups = merged[0]["children"]
    assert sorted(g["id"] for g in groups) == ["IDS", "Unbound"]


def test_merge_keeps_existing_label_does_not_overwrite():
    # a second fragment with the same id but a bare tag label must NOT clobber a real VisibleName
    plain = parse_menu("<menu><Services><Unbound><Advanced order='30' url='/ui/unbound/advanced'/></Unbound></Services></menu>")
    merged = merge_menus([_A, plain])
    unbound = merged[0]["children"][0]
    assert unbound["label"] == "Unbound DNS"  # kept from _A, not "Unbound"
    assert [c["id"] for c in unbound["children"]] == ["General", "Advanced"]  # children unioned, order-sorted
