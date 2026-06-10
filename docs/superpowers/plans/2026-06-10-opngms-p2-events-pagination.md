# P2.2 — Keyset Pagination for GET /events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Let API clients page deep into event history instead of only seeing the most-recent `limit` rows. Add opaque keyset-cursor pagination to `GET /api/tenants/{tenant_id}/events`.

**Architecture:** The `events` hypertable has composite PK `(time, device_id, source, event_key)`. Order by that tuple DESC and page with a Postgres row-value comparison (`(time,device_id,source,event_key) < cursor`). The endpoint returns an envelope `{items, next_cursor}`; the cursor is an opaque base64url(JSON) of the last row's PK tuple. Backend-only — the frontend does not consume this list (only the generated schema references it).

**Tech Stack:** FastAPI, SQLAlchemy async (raw `text()` SQL, parameterised), pydantic, pytest.

**Test env:** as other backend tasks (TimescaleDB `opngms_test`, `./.venv/bin/python -m pytest`, env vars exported). Branch: `p2-p3-hardening` (continue on the current branch — do NOT create a new one).

**Note on the "since client-side only" debt:** already handled — `app/services/ingest.py` passes `since` to the connector (`client.get_ids_alerts(since)`); the client-side `time > since` filter is only a best-effort safety net, and any further server-side filtering depends on the OPNsense device API (hardware-blocked). No change here.

---

## Task 1: Cursor codec + EventPage schema

**Files:**
- Modify: `backend/app/schemas/event.py`
- Modify: `backend/app/repositories/event.py` (add cursor encode/decode helpers)
- Test: `backend/tests/test_event_cursor.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_event_cursor.py`:

```python
import uuid
from datetime import datetime, timezone

import pytest

from app.repositories.event import decode_cursor, encode_cursor


def test_cursor_roundtrip():
    t = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
    did = uuid.uuid4()
    c = encode_cursor(t, did, "suricata", "abc123")
    t2, did2, source2, ek2 = decode_cursor(c)
    assert t2 == t and did2 == did and source2 == "suricata" and ek2 == "abc123"


def test_decode_rejects_garbage():
    with pytest.raises(ValueError):
        decode_cursor("not-a-valid-cursor!!")
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_event_cursor.py -q` → FAIL (no `encode_cursor`).

- [ ] **Step 3: Implement the codec**

In `backend/app/repositories/event.py`, add near the top (after imports):

```python
import base64
import json
import uuid as _uuid
from datetime import datetime


def encode_cursor(time: datetime, device_id: _uuid.UUID, source: str, event_key: str) -> str:
    raw = json.dumps([time.isoformat(), str(device_id), source, event_key]).encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_cursor(cursor: str) -> tuple[datetime, _uuid.UUID, str, str]:
    try:
        t, did, source, ek = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return datetime.fromisoformat(t), _uuid.UUID(did), source, ek
    except Exception as exc:  # noqa: BLE001
        raise ValueError("invalid cursor") from exc
```

Add the envelope schema in `backend/app/schemas/event.py`:

```python
class EventPage(BaseModel):
    items: list[EventOut]
    next_cursor: str | None = None
```

- [ ] **Step 4: Run to verify it passes** — same command. Expected: PASS.

- [ ] **Step 5: Commit**

```
git add backend/app/repositories/event.py backend/app/schemas/event.py backend/tests/test_event_cursor.py
git commit -m "feat(events): opaque keyset cursor codec + EventPage envelope"
```

---

## Task 2: Keyset repo method + paginated endpoint

**Files:**
- Modify: `backend/app/repositories/event.py` (add `list_page`)
- Modify: `backend/app/api/events.py` (envelope + `after` param)
- Test: `backend/tests/test_events_pagination.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_events_pagination.py`:

```python
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.repositories.event import EventRepository


@pytest.fixture
async def factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def _seed(factory, tid, did, n):
    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'T','t','active')"), {"i": tid})
        await s.execute(
            text("INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags)"
                 " VALUES (:i,:t,'d','https://d',''::bytea,''::bytea,true,'unverified','{}')"),
            {"i": did, "t": tid},
        )
        for k in range(n):
            await s.execute(
                text("INSERT INTO events (time,device_id,source,event_key,tenant_id,name)"
                     " VALUES (:tm,:d,'suricata',:ek,:t,:nm)"),
                {"tm": base + timedelta(minutes=k), "d": did, "ek": f"k{k}", "t": tid, "nm": f"e{k}"},
            )
        await s.commit()


async def test_keyset_pages_cover_all_rows_without_overlap(factory):
    tid, did = uuid.uuid4(), uuid.uuid4()
    await _seed(factory, tid, did, 5)
    async with factory() as s:
        repo = EventRepository(s, tid)
        page1, c1 = await repo.list_page(source=None, device_id=None, frm=None, to=None, after=None, limit=2)
        page2, c2 = await repo.list_page(source=None, device_id=None, frm=None, to=None, after=c1, limit=2)
        page3, c3 = await repo.list_page(source=None, device_id=None, frm=None, to=None, after=c2, limit=2)
    names = [e.name for e in page1 + page2 + page3]
    assert names == ["e4", "e3", "e2", "e1", "e0"]  # time DESC, no overlap/gap
    assert c1 is not None and c2 is not None
    assert c3 is None  # last page (fewer than limit) -> no next cursor
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_events_pagination.py -q` → FAIL (`list_page` missing).

- [ ] **Step 3: Implement `list_page`**

In `backend/app/repositories/event.py`, add to `EventRepository` (keep the existing `list`/`top`):

```python
    async def list_page(
        self,
        *,
        source: str | None,
        device_id: uuid.UUID | None,
        frm: datetime | None,
        to: datetime | None,
        after: str | None,
        limit: int,
    ) -> tuple[list[EventOut], str | None]:
        n = min(limit, MAX_EVENTS)
        clauses = ["tenant_id = :tid"]
        params: dict = {"tid": self.tenant_id, "limit": n}
        if source is not None:
            clauses.append("source = :source")
            params["source"] = source
        if device_id is not None:
            clauses.append("device_id = :did")
            params["did"] = device_id
        if frm is not None:
            clauses.append("time >= :frm")
            params["frm"] = frm
        if to is not None:
            clauses.append("time < :to")
            params["to"] = to
        if after is not None:
            c_time, c_did, c_source, c_ek = decode_cursor(after)
            clauses.append("(time, device_id, source, event_key) < (:c_time, :c_did, :c_source, :c_ek)")
            params |= {"c_time": c_time, "c_did": c_did, "c_source": c_source, "c_ek": c_ek}
        where = " AND ".join(clauses)
        sql = text(
            f"SELECT {_LIST_COLUMNS}, event_key FROM events WHERE {where} "
            "ORDER BY time DESC, device_id DESC, source DESC, event_key DESC LIMIT :limit"
        )
        rows = (await self.session.execute(sql, params)).mappings().all()
        items = [EventOut(**{k: v for k, v in dict(r).items() if k != "event_key"}) for r in rows]
        next_cursor = None
        if len(rows) == n:
            last = rows[-1]
            next_cursor = encode_cursor(last["time"], last["device_id"], last["source"], last["event_key"])
        return items, next_cursor
```

- [ ] **Step 4: Wire the endpoint**

In `backend/app/api/events.py`: import `EventPage` from `app.schemas.event` and `decode_cursor` is not needed at the API layer. Change the list endpoint:

```python
@router.get("/events", response_model=EventPage)
async def list_events(
    tenant_id: uuid.UUID,
    source: str | None = Query(None),
    device_id: uuid.UUID | None = Query(None),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    after: str | None = Query(None, description="Opaque keyset cursor from a previous page's next_cursor"),
    limit: int = Query(100, ge=1, le=MAX_EVENTS),
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> EventPage:
    repo = EventRepository(session, tenant_id)
    try:
        items, next_cursor = await repo.list_page(
            source=source, device_id=device_id,
            frm=_ensure_utc(from_), to=_ensure_utc(to), after=after, limit=limit,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid cursor")
    return EventPage(items=items, next_cursor=next_cursor)
```

Keep the old `repo.list(...)` method in place if other callers use it (grep first: `grep -rn "\.list(" backend/app | grep -i event`); if nothing else uses it, you may remove it and its now-unused parts, but do NOT break `EventOut`/`top`.

- [ ] **Step 5: Run to verify it passes**

`pytest tests/test_events_pagination.py tests/test_event_cursor.py -q` plus any existing events API test (`grep -rl events backend/tests`). Expected: PASS. Update any existing test that asserted `GET /events` returns a bare list — it now returns `{items, next_cursor}`.

- [ ] **Step 6: Regenerate the frontend schema (kept in sync)**

```
cd backend && DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test SESSION_SECRET=x \
  MASTER_KEY="$(./.venv/bin/python -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')" \
  ./.venv/bin/python scripts/export_openapi.py > ../frontend/openapi.json
cd ../frontend && npx openapi-typescript openapi.json -o src/api/schema.d.ts
```
(Frontend has no consumer of this list, so no component changes; just keep the generated types accurate. If `npm run gen:api` is easier, use it.)

- [ ] **Step 7: Commit**

```
git add backend/app/repositories/event.py backend/app/api/events.py backend/tests/test_events_pagination.py frontend/openapi.json frontend/src/api/schema.d.ts
git commit -m "feat(events): keyset-paginated GET /events ({items,next_cursor})"
```

---

## Final verification
- [ ] `cd backend && ./.venv/bin/python -m pytest -q` all green.
- [ ] Pagination covers all rows with no overlap/gap across pages (test asserts this).
