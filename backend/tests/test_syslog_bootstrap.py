import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # repo root (backend/tests/ -> repo)


class _FakeResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None


class _FakeHttpxClient:
    """No-op httpx.Client stand-in: the bootstrap's OpenSearch calls return 200, so the CLI's
    cert/CRL writing (the part under test) runs without a live OpenSearch."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def put(self, *a, **k):
        return _FakeResponse(200)

    def post(self, *a, **k):
        return _FakeResponse(200)

    def delete(self, *a, **k):
        return _FakeResponse(404)


async def test_bootstrap_writes_initial_crl(db_engine, tmp_path, monkeypatch):
    """syslog-bootstrap writes CA/server certs AND an initial (empty) hash-named CRL so crl-dir()
    has a valid file at first start."""
    import app.cli as cli

    monkeypatch.setattr(cli.httpx, "Client", _FakeHttpxClient)
    await cli.run_syslog_bootstrap(tmp_path, force=True, engine=db_engine)

    # CA + server identity were written.
    assert (tmp_path / "CA.pem").exists()
    assert (tmp_path / "server.pem").exists()
    assert (tmp_path / "server.key").exists()
    # The initial CRL is present, hash-named, and valid (empty ledger -> empty CRL).
    crls = list((tmp_path / "crl").glob("*.r0"))
    assert len(crls) == 1, crls
    from cryptography import x509
    crl = x509.load_pem_x509_crl(crls[0].read_bytes())
    assert len(crl) == 0


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
