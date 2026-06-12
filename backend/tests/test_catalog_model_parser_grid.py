from tools.opnsense_catalog.model_parser import parse_model

_XML = """
<model>
  <mount>//OPNsense/IDS</mount>
  <items>
    <general><enabled type="BooleanField"/></general>
    <userDefinedRules>
      <rule type="ArrayField">
        <enabled type="BooleanField"><default>1</default></enabled>
        <description type="TextField"/>
      </rule>
    </userDefinedRules>
  </items>
</model>
"""


def test_arrayfield_becomes_a_grid_with_item_fields():
    pm = parse_model(_XML)
    assert {f.path for f in pm.fields} == {"general.enabled"}
    assert len(pm.grids) == 1
    g = pm.grids[0]
    assert g.path == "userDefinedRules.rule"
    assert {f.path for f in g.fields} == {"enabled", "description"}
