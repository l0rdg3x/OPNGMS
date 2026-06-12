from tools.opnsense_catalog.form_parser import parse_forms

_FORM = """
<form>
  <field><id>general.enabled</id><label>Enabled</label><help>Turn IDS on</help></field>
  <field><type>header</type><label>Advanced</label></field>
  <field><id>general.detail</id><label>Detail level</label></field>
</form>
"""


def test_extracts_label_help_keyed_by_field_id():
    out = parse_forms([("general", _FORM)])
    assert out["general.enabled"]["label"] == "Enabled"
    assert out["general.enabled"]["help"] == "Turn IDS on"
    assert out["general.enabled"]["page"] == "general"
    assert out["general.detail"]["label"] == "Detail level"
    assert "" not in out
