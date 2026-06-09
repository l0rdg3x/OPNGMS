from app.services.config_diff import canonical_hash, structural_diff

BASE = (
    "<opnsense>"
    "<revision><time>1000</time><description>save A</description></revision>"
    "<system><hostname>fw1</hostname><user><password>secret1</password></user></system>"
    "</opnsense>"
)
# Only <revision> changed (re-save) -> must NOT count as drift.
RESAVED = (
    "<opnsense>"
    "<revision><time>2000</time><description>save B</description></revision>"
    "<system><hostname>fw1</hostname><user><password>secret1</password></user></system>"
    "</opnsense>"
)
# A real change: hostname + password changed.
CHANGED = (
    "<opnsense>"
    "<revision><time>3000</time><description>save C</description></revision>"
    "<system><hostname>fw2</hostname><user><password>secret2</password></user></system>"
    "</opnsense>"
)


def test_canonical_hash_ignores_revision_only_changes():
    assert canonical_hash(BASE) == canonical_hash(RESAVED)


def test_canonical_hash_detects_real_change():
    assert canonical_hash(BASE) != canonical_hash(CHANGED)


def test_structural_diff_reports_paths_without_values():
    changes = structural_diff(BASE, CHANGED)
    paths = {c["path"]: c["change"] for c in changes}
    assert paths["opnsense/system/hostname"] == "modified"
    assert paths["opnsense/system/user/password"] == "modified"
    # Secret-safe: no element values appear anywhere in the output.
    blob = repr(changes)
    assert "secret1" not in blob and "secret2" not in blob and "fw2" not in blob


def test_structural_diff_added_removed():
    a = "<opnsense><system><hostname>fw1</hostname></system></opnsense>"
    b = "<opnsense><system><hostname>fw1</hostname><dnsserver>1.1.1.1</dnsserver></system></opnsense>"
    changes = {c["path"]: c["change"] for c in structural_diff(a, b)}
    assert changes["opnsense/system/dnsserver"] == "added"
    changes2 = {c["path"]: c["change"] for c in structural_diff(b, a)}
    assert changes2["opnsense/system/dnsserver"] == "removed"


def test_rejects_billion_laughs_entity_expansion():
    import pytest

    bomb = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
        "<opnsense><x>&lol2;</x></opnsense>"
    )
    # defusedxml must refuse entity expansion (raise), not expand it.
    with pytest.raises(Exception):
        canonical_hash(bomb)


def test_rejects_external_entity_xxe():
    import pytest

    xxe = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        "<opnsense><x>&xxe;</x></opnsense>"
    )
    with pytest.raises(Exception):
        canonical_hash(xxe)
