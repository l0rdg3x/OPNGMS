import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # repo root (backend/tests/ -> repo)


def test_index_template_targets_logs():
    # The index template (mappings) is still applied; its glob covers the per-tenant index names
    # opngms-logs-<tenant_id>-DATE as well as any legacy opngms-logs-DATE indices.
    tpl = json.loads((ROOT / "deploy/opensearch/index-template.json").read_text())
    assert tpl["index_patterns"] == ["opngms-logs-*"]
    props = tpl["template"]["mappings"]["properties"]
    assert props["tenant_id"]["type"] == "keyword" and props["device_id"]["type"] == "keyword"


def test_global_ism_policy_is_retired():
    # SP-2: retention is per-tenant and owned by the worker (purge_log_lake). The global ISM policy file
    # is retired — its presence would imply a global age-based delete that violates per-tenant overrides.
    assert not (ROOT / "deploy/opensearch/ism-policy.json").exists()
