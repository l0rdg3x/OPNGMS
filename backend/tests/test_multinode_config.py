import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_multinode_compose_defines_three_nodes_no_single_node():
    data = yaml.safe_load((ROOT / "docker-compose.logs.multinode.yml").read_text())
    svcs = data["services"]
    for n in ("opensearch-n1", "opensearch-n2", "opensearch-n3"):
        assert n in svcs, f"missing {n}"
    assert "single-node" not in json.dumps(svcs)  # cluster discovery, not single-node
    assert "syslog-ng" in svcs and "syslog-bootstrap" in svcs


def test_multinode_index_template_is_replicated():
    tpl = json.loads((ROOT / "deploy/opensearch/index-template.multinode.json").read_text())
    settings = tpl["template"]["settings"]
    assert settings["number_of_replicas"] == 1
    assert settings["number_of_shards"] == 2
