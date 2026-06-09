from app.services.config_model import build_tree, is_sensitive

XML = (
    "<opnsense>"
    "<revision><time>1</time></revision>"
    "<system><hostname>fw1</hostname>"
    "<user><name>root</name><password>topsecret</password></user></system>"
    "<interfaces><wan><if>igb0</if></wan><lan><if>igb1</if></lan></interfaces>"
    "</opnsense>"
)


def test_is_sensitive():
    assert is_sensitive("password") and is_sensitive("api_key") and is_sensitive("PrivateKey")
    assert not is_sensitive("hostname") and not is_sensitive("if")


def test_is_sensitive_opnsense_secret_tags():
    # OPNsense literal secret tags that the original denylist missed.
    assert is_sensitive("privkey") and is_sensitive("cert_privkey")
    assert is_sensitive("md5-hash") and is_sensitive("nthash")
    assert is_sensitive("otp_seed")
    # Legitimate display fields must stay visible (no over-redaction here).
    assert not is_sensitive("hostname") and not is_sensitive("if") and not is_sensitive("descr")
    assert not is_sensitive("type") and not is_sensitive("ipaddr")


def test_privkey_leaf_is_redacted_and_never_emitted():
    import json

    xml = "<opnsense><cert><privkey>SUPERSECRETPRIVATEKEY</privkey></cert></opnsense>"
    root = build_tree(xml)
    privkey = root["children"][0]["children"][0]
    assert privkey["tag"] == "privkey"
    assert privkey["sensitive"] is True and privkey["value"] is None
    assert "SUPERSECRETPRIVATEKEY" not in json.dumps(root)


def test_build_tree_structure_and_order():
    root = build_tree(XML)
    assert root["tag"] == "opnsense"
    # <revision> stripped; order preserved
    top = [c["tag"] for c in root["children"]]
    assert top == ["system", "interfaces"]
    system = root["children"][0]
    hostname = system["children"][0]
    assert hostname["path"] == "opnsense/system/hostname"
    assert hostname["value"] == "fw1"
    assert hostname["sensitive"] is False


def test_sensitive_value_is_redacted_and_never_emitted():
    root = build_tree(XML)
    import json
    blob = json.dumps(root)
    assert "topsecret" not in blob  # secret never appears anywhere
    # locate the password node
    user = root["children"][0]["children"][1]
    pw = [c for c in user["children"] if c["tag"] == "password"][0]
    assert pw["sensitive"] is True and pw["value"] is None


def test_sensitive_container_redacts_whole_subtree():
    # A sensitive tag WITH children must redact the node and its descendants:
    # the descendant text must NOT leak and the node must be sensitive.
    import json

    xml = "<opnsense><privkey><inner>SECRET</inner></privkey></opnsense>"
    root = build_tree(xml)
    privkey = root["children"][0]
    assert privkey["tag"] == "privkey"
    assert privkey["sensitive"] is True
    inner = privkey["children"][0]
    assert inner["tag"] == "inner"
    assert inner["sensitive"] is True and inner["value"] is None
    assert "SECRET" not in json.dumps(root)


def test_sensitive_attribute_value_is_redacted():
    # A secret carried in an attribute must be nulled, never emitted verbatim.
    import json

    xml = '<opnsense><user password="ATTRSECRET">root</user></opnsense>'
    root = build_tree(xml)
    user = root["children"][0]
    assert user["attributes"]["password"] is None
    assert "ATTRSECRET" not in json.dumps(root)


def test_non_sensitive_encryption_subtree_not_redacted():
    # Regression: removing "crypt" from the denylist means <encryption> is no longer
    # over-matched; a non-sensitive subtree keeps its values and stays sensitive=False.
    import json

    xml = "<opnsense><encryption><cipher>aes-256-gcm</cipher></encryption></opnsense>"
    root = build_tree(xml)
    encryption = root["children"][0]
    assert encryption["tag"] == "encryption"
    assert encryption["sensitive"] is False
    cipher = encryption["children"][0]
    assert cipher["tag"] == "cipher"
    assert cipher["sensitive"] is False and cipher["value"] == "aes-256-gcm"
    assert "aes-256-gcm" in json.dumps(root)


def test_rejects_hostile_xml():
    import pytest

    bomb = '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "x"><!ENTITY b "&a;&a;">]><opnsense><x>&b;</x></opnsense>'
    with pytest.raises(Exception):
        build_tree(bomb)
