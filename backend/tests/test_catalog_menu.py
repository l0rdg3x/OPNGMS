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
