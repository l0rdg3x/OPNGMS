import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from tests.factories import make_membership, make_user


def _patch_search(monkeypatch, captured):
    import app.api.logs as mod
    from app.services.log_search import LogHit, SearchResult

    async def fake(settings, *, tenant_id, frm, to, query, device_id, page, size):
        captured["tenant_id"] = tenant_id
        captured["query"] = query
        return SearchResult(
            total=1,
            hits=[
                LogHit(
                    id="x",
                    timestamp="2026-06-01T00:00:00Z",
                    device_id="d",
                    host="fw",
                    program="filterlog",
                    message="m",
                    source={"a": 1},
                )
            ],
        )

    monkeypatch.setattr(mod, "search_logs", fake)


async def _seed(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        op = await make_user(s, email="op@x.io", password="pw12345")
        ro = await make_user(s, email="ro@x.io", password="pw12345")
        await s.execute(
            text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"),
            {"i": tid},
        )
        await make_membership(s, user_id=op.id, tenant_id=tid, role="operator")
        await make_membership(s, user_id=ro.id, tenant_id=tid, role="read_only")
        await set_tenant_context(s, tid)
        await s.execute(
            text(
                "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
                "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"
            ),
            {"i": did, "t": tid},
        )
        await s.commit()
    return tid, did


async def _login(api_client, email):
    r = await api_client.post("/api/login", json={"email": email, "password": "pw12345"})
    assert r.status_code == 200


async def test_operator_can_search_tenant_scoped(api_client, db_engine, monkeypatch):
    captured = {}
    _patch_search(monkeypatch, captured)
    tid, did = await _seed(db_engine)
    await _login(api_client, "op@x.io")
    r = await api_client.post(
        f"/api/tenants/{tid}/logs/search",
        json={"query": "action:block", "frm": "2026-06-01T00:00:00Z", "to": "2026-06-02T00:00:00Z"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1 and body["hits"][0]["source"] == {"a": 1}
    assert captured["tenant_id"] == tid  # tenant taken from the PATH, not the body
    assert captured["query"] == "action:block"


async def test_read_only_denied(api_client, db_engine):
    tid, _ = await _seed(db_engine)
    await _login(api_client, "ro@x.io")
    r = await api_client.post(
        f"/api/tenants/{tid}/logs/search",
        json={"frm": "2026-06-01T00:00:00Z", "to": "2026-06-02T00:00:00Z"},
    )
    assert r.status_code == 403


async def test_bad_range_400(api_client, db_engine, monkeypatch):
    _patch_search(monkeypatch, {})
    tid, _ = await _seed(db_engine)
    await _login(api_client, "op@x.io")
    r = await api_client.post(
        f"/api/tenants/{tid}/logs/search",
        json={"frm": "2026-06-02T00:00:00Z", "to": "2026-06-01T00:00:00Z"},
    )
    assert r.status_code == 400
