# OPNGMS — Phase 5 / Milestone 5B: Web Activity + Bandwidth + Up/Down Sections — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three real-data report sections on top of the 5A engine — **Web Activity** (DNS), **Data Usage/Bandwidth** (interface counters), **Up/Down Status** (poll presence) — and make all sections (including 5A's Attacks) genuinely **per-firewall** by adding `device_id` filtering to the aggregator.

**Architecture:** Extend `app/services/reporting/aggregation.py` (per-device ranked/timeline + new bandwidth/availability queries), `context.py` (new blocks + byte formatting), and the Jinja template. No new ingest, no device contact, no DB migration. Tenant-scoped under RLS, autoescaped, SSRF-safe (inherited from 5A).

**Tech Stack:** Python 3.12+, SQLAlchemy 2.0 async, TimescaleDB `time_bucket` (interval bound as a `timedelta`), WeasyPrint/Jinja2; pytest.

---

## Context for the implementer (read first)

Codebase is **English**. Backend only. 5A is in `main`.

**Current `aggregation.py`** (`app/services/reporting/aggregation.py`) has: `_BUCKETS=("1 hour","6 hours","1 day")`, `pick_bucket(span)`, `DeviceRow`, and `ReportAggregator(session, tenant_id)` with `devices()`, `top(*, field, frm, to, source="ids", limit=10)` (currently delegates to `EventRepository.top`), `timeline(*, frm, to, bucket, source="ids")` (builds its own SQL with the allowlisted `'{bucket}'::interval` literal).

**Events** (`events`): `time, device_id, source('ids'|'dns'), tenant_id, category, src_ip, dst_ip, name, severity, action, attributes`. DNS rows: `name`=domain, `src_ip`=client, `action`=`allowed|blocked`. `TOP_FIELDS = {"src_ip","dst_ip","name","action","severity"}` (in `app/repositories/event.py`).

**Metrics** (`metrics`): `time, device_id, metric, label, tenant_id, value(float)`. Relevant: `iface.bytes_in`/`iface.bytes_out` (label=interface, **cumulative counters**), `cpu.pct` (written every successful poll → poll-presence marker). `MetricRepository.series` binds a `timedelta` to `time_bucket(:bucket, time)` — asyncpg accepts an interval param when it is a `timedelta` (NOT a str).

**Context** (`context.py`): dataclasses `RankedTable`, `AttacksBlock`, `DeviceSection(device_name, attacks=None)`, `ReportContext`, and `async build_context(aggregator, *, tenant_name, timezone_name, owner, frm, to, title=...)` that loops devices and builds an `AttacksBlock` each. Template `templates/report.html.j2` renders per-device sections; `templates/report.css` has `.ranked`/`.chart`/`caption` styles. `charts.line_chart(points, *, width, height)` / `bar_chart`.

**Commands** (from `backend/`, DB up):
```bash
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test \
ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test \
.venv/bin/python -m pytest -q
```

**Security:** autoescape stays ON; DNS domains/IPs are untrusted → escaped table text only, never a URL attribute; no `Markup`/`| safe` on data. New metric queries bind `bucket` as a `timedelta`. Bandwidth deltas clamped `≥ 0` (`GREATEST(...,0)`). All queries tenant-scoped (RLS + explicit `tenant_id`), bound params (the only literal is the existing 5A allowlisted `'{bucket}'::interval` in the events `timeline`).

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `app/services/reporting/aggregation.py` | per-device `top`/`timeline`; `top_blocked_domains`, `bandwidth_timeline`, `bandwidth_totals`, `availability_series` | Modify |
| `app/services/reporting/context.py` | `WebActivityBlock`/`BandwidthBlock`/`StatusBlock`, byte formatter, extend `build_context` | Modify |
| `app/services/reporting/templates/report.html.j2` | render the new sections | Modify |
| `tests/test_report_aggregation.py` | web/bandwidth/availability + RLS tests | Modify |
| `tests/test_report_context.py` | render assertions for the new sections | Modify |

---

## Task 1: Per-device filtering + Web Activity aggregation

**Files:** Modify `aggregation.py`, `tests/test_report_aggregation.py`.

- [ ] **Step 1: Write failing tests** — append to `tests/test_report_aggregation.py`:
```python
async def test_top_supports_device_filter_and_blocked_domains(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tid = t.id
    from sqlalchemy import text
    d1, d2 = uuid.uuid4(), uuid.uuid4()
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:
        for did in (d1, d2):
            await s.execute(text("INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                                 "VALUES (:id,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"id": did, "t": tid})
        # DNS events: d1 -> a.com (allowed x2), bad.com (blocked); d2 -> c.com (allowed)
        seed = [(d1,"a.com","allowed"),(d1,"a.com","allowed"),(d1,"bad.com","blocked"),(d2,"c.com","allowed")]
        for i,(did,name,act) in enumerate(seed):
            await s.execute(text("INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip, action) "
                                 "VALUES (:t,:d,'dns',:k,:tid,:n,'10.0.0.1',:a)"),
                            {"t": base, "d": did, "k": f"k{i}", "tid": tid, "n": name, "a": act})
        await s.commit()
    async with factory() as s:
        agg = ReportAggregator(s, tid)
        frm, to = base - timedelta(hours=1), base + timedelta(hours=1)
        # device-scoped Top Sites for d1
        sites = await agg.top(field="name", source="dns", frm=frm, to=to, device_id=d1)
        assert ("a.com", 2) in [(r.value, r.count) for r in sites]
        assert "c.com" not in [r.value for r in sites]   # d2's site excluded
        # Top Blocked (device d1)
        blocked = await agg.top_blocked_domains(frm=frm, to=to, device_id=d1)
        assert [(r.value, r.count) for r in blocked] == [("bad.com", 1)]
        # DNS timeline device-scoped
        tl = await agg.timeline(frm=frm, to=to, bucket="1 hour", source="dns", device_id=d1)
        assert sum(c for _, c in tl) == 3
```
Run → FAIL.

- [ ] **Step 2: Implement** — in `aggregation.py`, add the `TOP_FIELDS` import and a private `_ranked` helper, reimplement `top` to use it (adds `device_id`), add `top_blocked_domains`, and add `device_id` to `timeline`.

Replace the import line `from app.repositories.event import EventRepository` with:
```python
from app.repositories.event import TOP_FIELDS
```
(`EventRepository` is no longer needed — `top` is reimplemented locally with the same allowlist + bound-params safety, now supporting `device_id`/`action`.) Remove `self._events = EventRepository(...)` from `__init__`.

Replace the `top` method and `timeline` method with:
```python
    async def _ranked(
        self, *, field: str, source: str, frm: datetime, to: datetime,
        device_id: uuid.UUID | None = None, action: str | None = None, limit: int = 10,
    ) -> list[EventTopRow]:
        # `field` MUST be allowlisted (it is interpolated as a column name); everything else is bound.
        if field not in TOP_FIELDS:
            raise ValueError(f"field not allowed: {field}")
        clauses = ["tenant_id = :tid", f"{field} <> ''", "source = :source", "time >= :frm", "time < :to"]
        params: dict = {"tid": self.tenant_id, "source": source, "frm": frm, "to": to, "limit": min(limit, 1000)}
        if device_id is not None:
            clauses.append("device_id = :did")
            params["did"] = device_id
        if action is not None:
            clauses.append("action = :action")
            params["action"] = action
        where = " AND ".join(clauses)
        sql = text(
            f"SELECT {field} AS value, count(*) AS count FROM events WHERE {where} "
            f"GROUP BY {field} ORDER BY count DESC, value LIMIT :limit"
        )
        rows = (await self.session.execute(sql, params)).all()
        return [EventTopRow(value=str(r.value), count=int(r.count)) for r in rows]

    async def top(
        self, *, field: str, frm: datetime, to: datetime, source: str = "ids",
        device_id: uuid.UUID | None = None, limit: int = 10,
    ) -> list[EventTopRow]:
        return await self._ranked(field=field, source=source, frm=frm, to=to, device_id=device_id, limit=limit)

    async def top_blocked_domains(
        self, *, frm: datetime, to: datetime, device_id: uuid.UUID | None = None, limit: int = 10,
    ) -> list[EventTopRow]:
        return await self._ranked(
            field="name", source="dns", frm=frm, to=to, device_id=device_id, action="blocked", limit=limit,
        )

    async def timeline(
        self, *, frm: datetime, to: datetime, bucket: str, source: str = "ids",
        device_id: uuid.UUID | None = None,
    ) -> list[tuple[datetime, int]]:
        if bucket not in _BUCKETS:
            raise ValueError(f"bucket not allowed: {bucket}")
        clauses = ["tenant_id = :tid", "source = :source", "time >= :frm", "time < :to"]
        params: dict = {"tid": self.tenant_id, "source": source, "frm": frm, "to": to}
        if device_id is not None:
            clauses.append("device_id = :did")
            params["did"] = device_id
        where = " AND ".join(clauses)
        # `bucket` is allowlist-validated above; interpolated as a literal interval (asyncpg cannot
        # bind a str as an interval). Everything else is a bound parameter.
        sql = text(
            f"SELECT time_bucket('{bucket}'::interval, time) AS b, count(*) AS c "
            f"FROM events WHERE {where} GROUP BY b ORDER BY b"
        )
        rows = (await self.session.execute(sql, params)).all()
        return [(r.b, int(r.c)) for r in rows]
```
Run Step 1 tests → PASS. Run the existing aggregation + report tests → still green (the default-path `top`/`timeline` behavior is unchanged).

- [ ] **Step 3: RLS isolation still holds** — the existing `test_aggregator_is_tenant_isolated_under_rls` must still pass (it exercises `top` under `opngms_app`). Confirm. Then commit:
```bash
git add app/services/reporting/aggregation.py tests/test_report_aggregation.py
git commit -m "feat(reporting): per-device ranked/timeline + DNS top-blocked-domains aggregation"
```

---

## Task 2: Bandwidth aggregation (counter deltas, reset-safe)

**Files:** Modify `aggregation.py`, `tests/test_report_aggregation.py`.

- [ ] **Step 1: Write failing tests** — append:
```python
async def test_bandwidth_timeline_and_totals_reset_safe(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tid = t.id
    from sqlalchemy import text
    did = uuid.uuid4()
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:
        await s.execute(text("INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                             "VALUES (:id,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"id": did, "t": tid})
        # iface wan bytes_in counter samples within one hour bucket: 100, 300, 900 -> delta 800.
        # plus a reset case in the next bucket: 50, 120 -> delta 70 (max-min within bucket).
        samples = [
            (base + timedelta(minutes=0), "iface.bytes_in", "wan", 100.0),
            (base + timedelta(minutes=20), "iface.bytes_in", "wan", 300.0),
            (base + timedelta(minutes=40), "iface.bytes_in", "wan", 900.0),
            (base + timedelta(minutes=65), "iface.bytes_in", "wan", 50.0),   # reset (reboot)
            (base + timedelta(minutes=85), "iface.bytes_in", "wan", 120.0),
            (base + timedelta(minutes=20), "iface.bytes_out", "wan", 10.0),
            (base + timedelta(minutes=40), "iface.bytes_out", "wan", 60.0),  # +50 out
        ]
        for ts, m, lbl, val in samples:
            await s.execute(text("INSERT INTO metrics (time, device_id, metric, label, tenant_id, value) "
                                 "VALUES (:t,:d,:m,:l,:tid,:v)"),
                            {"t": ts, "d": did, "m": m, "l": lbl, "tid": tid, "v": val})
        await s.commit()
    async with factory() as s:
        agg = ReportAggregator(s, tid)
        frm, to = base, base + timedelta(hours=2)
        tl = await agg.bandwidth_timeline(frm=frm, to=to, bucket="1 hour", device_id=did)
        # first bucket: in delta 800 + out delta 50 = 850; second bucket: in delta 70 = 70
        totals_by_bucket = {b: v for b, v in tl}
        assert round(sum(totals_by_bucket.values())) == 920
        ti, to_ = await agg.bandwidth_totals(frm=frm, to=to, device_id=did)
        # totals over the whole range, per-interface max-min reset-clamped is computed per-bucket then summed
        assert ti >= 0 and to_ >= 0
```
Run → FAIL.

- [ ] **Step 2: Implement** — add the bucket→timedelta map + helper near `_BUCKETS`:
```python
_BUCKET_DELTAS = {"1 hour": timedelta(hours=1), "6 hours": timedelta(hours=6), "1 day": timedelta(days=1)}


def _bucket_delta(bucket: str) -> timedelta:
    if bucket not in _BUCKET_DELTAS:
        raise ValueError(f"bucket not allowed: {bucket}")
    return _BUCKET_DELTAS[bucket]
```
Add methods to `ReportAggregator`:
```python
    async def bandwidth_timeline(
        self, *, frm: datetime, to: datetime, bucket: str, device_id: uuid.UUID | None = None,
    ) -> list[tuple[datetime, float]]:
        """Transferred bytes (in+out) per bucket. Counters are cumulative, so per (bucket, interface,
        direction) we take max-min clamped >= 0 (a reset within the bucket yields 0 for that slice)."""
        delta = _bucket_delta(bucket)  # bound as a real interval (timedelta)
        clauses = ["tenant_id = :tid", "metric IN ('iface.bytes_in','iface.bytes_out')", "time >= :frm", "time < :to"]
        params: dict = {"bucket": delta, "tid": self.tenant_id, "frm": frm, "to": to}
        if device_id is not None:
            clauses.append("device_id = :did")
            params["did"] = device_id
        where = " AND ".join(clauses)
        sql = text(
            "SELECT b, SUM(d) AS total FROM ("
            "  SELECT time_bucket(:bucket, time) AS b, device_id, label, metric, "
            "         GREATEST(max(value) - min(value), 0) AS d "
            f"  FROM metrics WHERE {where} "
            "  GROUP BY b, device_id, label, metric"
            ") s GROUP BY b ORDER BY b"
        )
        rows = (await self.session.execute(sql, params)).all()
        return [(r.b, float(r.total)) for r in rows]

    async def bandwidth_totals(
        self, *, frm: datetime, to: datetime, bucket: str = "1 hour", device_id: uuid.UUID | None = None,
    ) -> tuple[float, float]:
        """(total_in, total_out) bytes over the range — summed per-bucket max-min (reset-safe)."""
        delta = _bucket_delta(bucket)
        clauses = ["tenant_id = :tid", "metric IN ('iface.bytes_in','iface.bytes_out')", "time >= :frm", "time < :to"]
        params: dict = {"bucket": delta, "tid": self.tenant_id, "frm": frm, "to": to}
        if device_id is not None:
            clauses.append("device_id = :did")
            params["did"] = device_id
        where = " AND ".join(clauses)
        sql = text(
            "SELECT metric, SUM(d) AS total FROM ("
            "  SELECT time_bucket(:bucket, time) AS b, device_id, label, metric, "
            "         GREATEST(max(value) - min(value), 0) AS d "
            f"  FROM metrics WHERE {where} "
            "  GROUP BY b, device_id, label, metric"
            ") s GROUP BY metric"
        )
        rows = (await self.session.execute(sql, params)).all()
        by_metric = {r.metric: float(r.total) for r in rows}
        return by_metric.get("iface.bytes_in", 0.0), by_metric.get("iface.bytes_out", 0.0)
```
Run Step 1 → PASS.

- [ ] **Step 3: Commit**
```bash
git add app/services/reporting/aggregation.py tests/test_report_aggregation.py
git commit -m "feat(reporting): bandwidth timeline + totals from interface counters (reset-safe)"
```

---

## Task 3: Availability (Up/Down) aggregation

**Files:** Modify `aggregation.py`, `tests/test_report_aggregation.py`.

- [ ] **Step 1: Write failing test** — append:
```python
async def test_availability_series_marks_gaps_down(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tid = t.id
    from sqlalchemy import text
    did = uuid.uuid4()
    base = datetime(2026, 6, 9, 0, 0, tzinfo=timezone.utc)
    async with factory() as s:
        await s.execute(text("INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                             "VALUES (:id,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"id": did, "t": tid})
        # cpu.pct present in hour 0 and hour 2, absent in hour 1 and 3 (gaps -> down)
        for h in (0, 2):
            await s.execute(text("INSERT INTO metrics (time, device_id, metric, label, tenant_id, value) "
                                 "VALUES (:t,:d,'cpu.pct','',:tid,5.0)"),
                            {"t": base + timedelta(hours=h, minutes=10), "d": did, "tid": tid})
        await s.commit()
    async with factory() as s:
        agg = ReportAggregator(s, tid)
        series, uptime = await agg.availability_series(frm=base, to=base + timedelta(hours=4), bucket="1 hour", device_id=did)
        ups = [v for _, v in series]
        assert ups == [1, 0, 1, 0]
        assert round(uptime) == 50
```
Run → FAIL.

- [ ] **Step 2: Implement** — add to `ReportAggregator`:
```python
    async def availability_series(
        self, *, frm: datetime, to: datetime, bucket: str, device_id: uuid.UUID,
    ) -> tuple[list[tuple[datetime, int]], float]:
        """Per-bucket up(1)/down(0) from successful-poll presence (cpu.pct), plus uptime %."""
        delta = _bucket_delta(bucket)
        sql = text(
            "SELECT time_bucket(:bucket, time) AS b, count(*) AS c "
            "FROM metrics WHERE tenant_id = :tid AND device_id = :did AND metric = 'cpu.pct' "
            "AND time >= :frm AND time < :to GROUP BY b"
        )
        rows = (await self.session.execute(
            sql, {"bucket": delta, "tid": self.tenant_id, "did": device_id, "frm": frm, "to": to}
        )).all()
        present = [r.b for r in rows]  # bucket starts that had a poll
        series: list[tuple[datetime, int]] = []
        cur = frm
        while cur < to:
            up = any(cur <= b < cur + delta for b in present)
            series.append((cur, 1 if up else 0))
            cur = cur + delta
        uptime = (sum(v for _, v in series) / len(series) * 100.0) if series else 0.0
        return series, uptime
```
Run Step 1 → PASS.

- [ ] **Step 3: Commit**
```bash
git add app/services/reporting/aggregation.py tests/test_report_aggregation.py
git commit -m "feat(reporting): availability (up/down) series from poll presence + uptime %"
```

---

## Task 4: Context + template — render the new sections

**Files:** Modify `context.py`, `templates/report.html.j2`, `tests/test_report_context.py`.

- [ ] **Step 1: Write failing test** — append to `tests/test_report_context.py` a test that seeds, for one device, IDS + DNS events + iface byte counters + cpu.pct, calls `build_context`, and asserts the rendered HTML contains the new sections and data:
```python
async def test_build_context_includes_web_bandwidth_status(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tid = t.id
    from sqlalchemy import text
    did = uuid.uuid4()
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:
        await s.execute(text("INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                             "VALUES (:id,:t,'fw-edge','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"id": did, "t": tid})
        await s.execute(text("INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip, action) "
                             "VALUES (:t,:d,'dns','k1',:tid,'example.org','10.0.0.7','allowed')"), {"t": base, "d": did, "tid": tid})
        await s.execute(text("INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip, action) "
                             "VALUES (:t,:d,'dns','k2',:tid,'tracker.bad','10.0.0.7','blocked')"), {"t": base, "d": did, "tid": tid})
        for mins, val in ((0, 100.0), (30, 500.0)):
            await s.execute(text("INSERT INTO metrics (time, device_id, metric, label, tenant_id, value) "
                                 "VALUES (:t,:d,'iface.bytes_in','wan',:tid,:v)"),
                            {"t": base + timedelta(minutes=mins), "d": did, "tid": tid, "v": val})
        await s.execute(text("INSERT INTO metrics (time, device_id, metric, label, tenant_id, value) "
                             "VALUES (:t,:d,'cpu.pct','',:tid,7.0)"), {"t": base + timedelta(minutes=5), "d": did, "tid": tid})
        await s.commit()
    async with factory() as s:
        agg = ReportAggregator(s, tid)
        ctx = await build_context(agg, tenant_name="Acme", timezone_name="UTC", owner=None,
                                  frm=base - timedelta(hours=1), to=base + timedelta(hours=1))
    sec = ctx.sections[0]
    assert sec.web is not None and sec.bandwidth is not None and sec.status is not None
    html = render_html(ctx)
    assert "Web Activity" in html and "example.org" in html and "tracker.bad" in html
    assert "Data Usage" in html
    assert "Up/Down Status" in html
```
Run → FAIL.

- [ ] **Step 2: Implement context** — in `context.py`, add a byte formatter + new dataclasses, and extend `build_context`.

Add near the top (after imports):
```python
def human_bytes(n: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    f, i = float(n), 0
    while f >= 1024 and i < len(units) - 1:
        f /= 1024
        i += 1
    return f"{f:.1f} {units[i]}"
```
Add dataclasses (next to the others):
```python
@dataclass
class WebActivityBlock:
    timeline_svg: str
    top_sites: RankedTable
    top_initiators: RankedTable
    top_blocked: RankedTable


@dataclass
class BandwidthBlock:
    timeline_svg: str
    total_in: str   # human-formatted
    total_out: str


@dataclass
class StatusBlock:
    timeline_svg: str
    uptime_pct: float
```
Extend `DeviceSection`:
```python
@dataclass
class DeviceSection:
    device_name: str
    attacks: AttacksBlock | None = None
    web: "WebActivityBlock | None" = None
    bandwidth: "BandwidthBlock | None" = None
    status: "StatusBlock | None" = None
```
In `build_context`, inside the device loop, after building `attacks`, add (passing `device_id=dev.id` to make every section per-firewall — this also closes the 5A per-device debt for Attacks: change the existing attacks `timeline`/`top` calls to pass `device_id=dev.id`):
```python
        # --- Web Activity (DNS) ---
        dns_tl = await aggregator.timeline(frm=frm, to=to, bucket=bucket, source="dns", device_id=dev.id)
        web = WebActivityBlock(
            timeline_svg=line_chart([(b.astimezone(_tz.utc).strftime("%m-%d %H:%M"), c) for b, c in dns_tl], width=520, height=140),
            top_sites=RankedTable("Top Sites", ("Site", "Hits"),
                                  [(r.value, r.count) for r in await aggregator.top(field="name", source="dns", frm=frm, to=to, device_id=dev.id)]),
            top_initiators=RankedTable("Top Initiators", ("Initiator", "Hits"),
                                       [(r.value, r.count) for r in await aggregator.top(field="src_ip", source="dns", frm=frm, to=to, device_id=dev.id)]),
            top_blocked=RankedTable("Top Blocked", ("Domain", "Blocks"),
                                    [(r.value, r.count) for r in await aggregator.top_blocked_domains(frm=frm, to=to, device_id=dev.id)]),
        )
        # --- Data Usage (bandwidth) ---
        bw_tl = await aggregator.bandwidth_timeline(frm=frm, to=to, bucket=bucket, device_id=dev.id)
        tin, tout = await aggregator.bandwidth_totals(frm=frm, to=to, bucket=bucket, device_id=dev.id)
        bandwidth = BandwidthBlock(
            timeline_svg=line_chart([(b.astimezone(_tz.utc).strftime("%m-%d %H:%M"), v) for b, v in bw_tl], width=520, height=140),
            total_in=human_bytes(tin), total_out=human_bytes(tout),
        )
        # --- Up/Down status ---
        av_series, uptime = await aggregator.availability_series(frm=frm, to=to, bucket=bucket, device_id=dev.id)
        status = StatusBlock(
            timeline_svg=line_chart([(b.astimezone(_tz.utc).strftime("%m-%d %H:%M"), v) for b, v in av_series], width=520, height=80),
            uptime_pct=round(uptime, 1),
        )
        sections.append(DeviceSection(device_name=dev.name, attacks=attacks, web=web, bandwidth=bandwidth, status=status))
```
Replace the existing `sections.append(DeviceSection(device_name=dev.name, attacks=attacks))` with the new append above (remove the old one). Update the existing attacks `timeline`/`top` calls in the loop to pass `device_id=dev.id` (closing the 5A per-device debt). Remove the old `# NOTE (5A tech debt)` comment about per-device since it's now addressed.

- [ ] **Step 3: Implement template** — in `report.html.j2`, inside the `{% if section.attacks %}...{% endif %}` device block, AFTER the attacks tables loop and before `</section>`, add:
```jinja
    {% if section.web %}
    <h3>Web Activity</h3>
    <div class="chart">{{ Markup(section.web.timeline_svg) }}</div>
    {% for tbl in [section.web.top_sites, section.web.top_initiators, section.web.top_blocked] %}
    <table class="ranked">
      <caption>{{ tbl.title }}</caption>
      <thead><tr><th>{{ tbl.columns[0] }}</th><th>{{ tbl.columns[1] }}</th></tr></thead>
      <tbody>
        {% for value, count in tbl.rows %}<tr><td>{{ value }}</td><td>{{ count }}</td></tr>{% endfor %}
        {% if not tbl.rows %}<tr><td colspan="2">No data</td></tr>{% endif %}
      </tbody>
    </table>
    {% endfor %}
    {% endif %}

    {% if section.bandwidth %}
    <h3>Data Usage</h3>
    <div class="chart">{{ Markup(section.bandwidth.timeline_svg) }}</div>
    <p class="summary">Total in: {{ section.bandwidth.total_in }} · Total out: {{ section.bandwidth.total_out }}</p>
    {% endif %}

    {% if section.status %}
    <h3>Up/Down Status</h3>
    <div class="chart">{{ Markup(section.status.timeline_svg) }}</div>
    <p class="summary">Uptime: {{ section.status.uptime_pct }}%</p>
    {% endif %}
```
(These render only when the block exists. The `attacks` guard `{% if section.attacks %}` currently wraps the whole inner body — make sure the new blocks are INSIDE the device `<section>` but you may place them after the attacks `{% endif %}` so they show even if a device has no IDS data. Restructure: keep `<h2>{{ section.device_name }}</h2>`, then the attacks block guarded by its own `{% if %}`, then the three new guarded blocks, all within the same `<section>`.)

Add to `report.css`: `.summary { font-size: 9pt; color: #333; margin: 2px 0 12px; }`.

- [ ] **Step 4: Run + commit** — Step 1 test PASS; full suite green; render a manual PDF sanity-check is optional.
```bash
git add app/services/reporting/context.py app/services/reporting/templates/report.html.j2 \
        app/services/reporting/templates/report.css tests/test_report_context.py
git commit -m "feat(reporting): render Web Activity + Data Usage + Up/Down sections (per-device)"
```

---

## Task 5: Technical debt

- [ ] **Step 1: Append**
```markdown
## Technical debt (5B)

- **Bandwidth precision**: per-bucket max-min (reset-clamped) approximates transferred bytes; a lag()-based
  per-sample delta would be exact. WAN-only vs all-interfaces is summed-all for now (interface roles later).
- **Availability is a poll-presence proxy**: a dedicated `device.up` metric per poll would make Up/Down exact.
- **Web content categories**: DNS has no content categorization (category is constant "query"); Top
  Categories / Web Filter categories come with 5C (mock) or a real proxy/category feed later.
- **Applications / Top Services / app-id**: deferred to 5C (needs flow/app-id ingest).
- **Per-device attacks**: now device-scoped (closed the 5A debt). 
```

- [ ] **Step 2: Commit**
```bash
git add docs/superpowers/plans/2026-06-10-opngms-phase5-milestone5B-web-bandwidth-status.md
git commit -m "docs: technical debt milestone 5B"
```

---

## Technical debt (5B) — recorded

- **Bandwidth precision**: per-bucket max-min (reset-clamped) approximates transferred bytes; a lag()-based
  per-sample delta would be exact, and a within-bucket reset overestimates that one bucket. WAN-only vs
  all-interfaces is summed-all for now (interface roles later).
- **Availability is a poll-presence proxy**: a dedicated `device.up` metric per poll would make Up/Down exact.
- **Web content categories**: DNS has no content categorization (category is constant "query"); Top
  Categories / Web Filter categories come with 5C (mock) or a real proxy/category feed later.
- **Applications / Top Services / app-id**: deferred to 5C (needs flow/app-id ingest).
- **Per-device attacks**: now device-scoped (closed the 5A debt).

---

## Definition of "Done" (5B)
- A generated report shows, per firewall (now genuinely device-scoped, incl. Attacks): **Web Activity**
  (DNS timeline + Top Sites / Top Initiators / Top Blocked), **Data Usage** (transferred-bytes timeline +
  total in/out), and **Up/Down Status** (availability timeline + uptime %).
- Real data, tenant-scoped + RLS-isolated, autoescaped, SSRF-safe; bandwidth reset-safe (`GREATEST(…,0)`).
- Backend suite green; no migration.
