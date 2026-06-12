from tools.opnsense_catalog.model_parser import parse_model

_XML = """
<model>
  <mount>//OPNsense/IDS</mount>
  <items>
    <general>
      <enabled type="BooleanField"><default>0</default><Required>N</Required></enabled>
      <detail type="TextField"/>
      <maxpkt type="IntegerField"><default>1500</default></maxpkt>
      <homenet type="NetworkField"><Multiple>Y</Multiple></homenet>
      <ruleset type="OptionField">
        <OptionValues><et>ET open</et><abuse>Abuse.ch</abuse></OptionValues>
      </ruleset>
      <categories type="OptionField">
        <Multiple>Y</Multiple><OptionValues><a>A</a><b>B</b></OptionValues>
      </categories>
      <weirdo type="SomeFutureField"/>
    </general>
  </items>
</model>
"""


def test_parses_scalar_field_classes_with_paths_and_types():
    pm = parse_model(_XML)
    assert pm.mount == "//OPNsense/IDS"
    by_path = {f.path: f for f in pm.fields}
    assert by_path["general.enabled"].type == "bool"
    assert by_path["general.enabled"].default == "0"
    assert by_path["general.detail"].type == "string"
    assert by_path["general.maxpkt"].type == "int" and by_path["general.maxpkt"].default == "1500"
    assert by_path["general.homenet"].type == "network"            # NetworkField stays network (Multiple is a UI hint)
    assert by_path["general.ruleset"].type == "enum"
    assert by_path["general.ruleset"].options == ["et", "abuse"]    # API values (tags), not labels
    assert by_path["general.categories"].type == "multienum"       # OptionField + Multiple -> multienum
    assert by_path["general.categories"].options == ["a", "b"]


def test_option_value_attribute_is_used_over_text():
    # Pattern B: <opt value="x">label</opt> -> the API value is the `value` attr, not the text.
    xml = ("<model><mount>//OPNsense/Unbound</mount><items><general>"
           "<zone type='OptionField'><OptionValues>"
           "<o1 value='transparent'>Transparent</o1><o2 value='static'>Static</o2>"
           "</OptionValues></zone></general></items></model>")
    pm = parse_model(xml)
    zone = next(f for f in pm.fields if f.path == "general.zone")
    assert zone.options == ["transparent", "static"]


def test_unknown_field_class_is_raw_never_dropped():
    pm = parse_model(_XML)
    weird = next(f for f in pm.fields if f.path == "general.weirdo")
    assert weird.type == "string" and weird.confidence == "raw"
