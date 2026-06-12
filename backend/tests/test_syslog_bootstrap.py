import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # repo root (backend/tests/ -> repo)


def test_index_template_targets_logs():
    tpl = json.loads((ROOT / "deploy/opensearch/index-template.json").read_text())
    assert tpl["index_patterns"] == ["opngms-logs-*"]
    props = tpl["template"]["mappings"]["properties"]
    assert props["tenant_id"]["type"] == "keyword" and props["device_id"]["type"] == "keyword"


def test_ism_retention_token_substitutes():
    raw = (ROOT / "deploy/opensearch/ism-policy.json").read_text().replace("{{RETENTION_DAYS}}", "30")
    pol = json.loads(raw)
    cond = pol["policy"]["states"][0]["transitions"][0]["conditions"]["min_index_age"]
    assert cond == "30d"
