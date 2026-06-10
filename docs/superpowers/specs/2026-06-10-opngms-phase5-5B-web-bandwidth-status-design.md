# OPNGMS — Phase 5 / Milestone 5B: Web Activity + Bandwidth + Up/Down Sections — Design Spec

- **Date:** 2026-06-10
- **Status:** Approved (the user approved proceeding through 5B–5E autonomously after the 5A sample)
- **Phase:** 5 of 5 — Milestone 5B (real-data report sections on top of the 5A engine)
- **Depends on:** 5A (reporting engine: aggregation/context/template/service/api), 3B (DNS events), 2 (metrics) — all in `main`
- **Enables:** 5C (applications/web-filter + threat-level), 5D (white-label config), 5E (scheduled + history)

---

## 1. Context

5A shipped the PDF engine + the **Attacks** section. 5B fills three more reference-layout sections with
**real data already in the database**, using the same engine (aggregation → context → template → SVG):

- **Web Activity** ("sites visited") — from **DNS events** (3B).
- **Data Usage / Bandwidth** ("banda") — from **interface byte counters** (2).
- **Up/Down Status timeline** — derived from **successful-poll presence** (2).

No new ingest, no device contact, no migration. Everything stays tenant-scoped under RLS, autoescaped,
SSRF-safe (inherited from 5A).

## 2. Available data (verified)

- **DNS events** (`events`, `source='dns'`): `name` = queried domain, `src_ip` = client (initiator),
  `action` = `allowed`|`blocked`, `category` = constant `"query"` (no content categorization), `time`.
- **Metrics** (`metrics`, key/label/value): `iface.bytes_in` / `iface.bytes_out` (label = interface
  name, **cumulative counters**), `iface.up`, `cpu.pct` (poll-presence marker), etc. `MetricRepository`
  already binds a `timedelta` to `time_bucket(:bucket, time)` (asyncpg accepts an interval param).
- **No explicit device up/down time series** — only the current `device.status` field. 5B derives an
  availability timeline from poll presence (a bucket with any successful-poll metric row → "up").

## 3. Design decisions (5B)

| Topic | Decision |
|-------|----------|
| Web Activity | Timeline (DNS events bucketed) + **Top Sites** (`name`) + **Top Initiators** (`src_ip`) + **Top Blocked** (`name` where `action='blocked'`). Content **categories** don't exist → omitted here (a "No data" row / deferred to 5C). |
| Bandwidth | `iface.bytes_in`/`iface.bytes_out` are **cumulative counters** → compute **per-bucket deltas, reset-safe** (a negative delta = counter reset/reboot → contributes 0), summed across interfaces → a transferred-bytes timeline (in + out) + total in/out summary. |
| Up/Down | **Availability timeline** per device: for each bucket, "up" if a successful-poll marker metric (`cpu.pct`) exists in that bucket, else "down". A proxy, but a real signal (a polled device wrote metrics). |
| Bucket width | Reuse 5A's `pick_bucket(span)` for all timelines (consistency). |
| Charts | Reuse `line_chart` (timelines) + `bar_chart` (optional). Bandwidth in/out can be two `line_chart`s or one combined; keep simple (one transferred-bytes line in 5B). |
| Scope | Real data only; the app-id-dependent ranked blocks (Top Services/Applications, Web Usage By Site nesting) stay deferred to 5C (mock). |

## 4. Aggregation additions (`app/services/reporting/aggregation.py`)

- **DNS** (reuse + extend):
  - `timeline(..., source="dns")` already exists (parameterized by source) → DNS timeline for free.
  - `top(field="name"/"src_ip", source="dns", ...)` already works → Top Sites / Top Initiators.
  - **New** `top_blocked_domains(frm, to, limit)`: a tenant-scoped query
    `SELECT name AS value, count(*) AS count FROM events WHERE tenant_id=:tid AND source='dns'
     AND action='blocked' AND name<>'' AND time>=:frm AND time<:to GROUP BY name ORDER BY count DESC,
     value LIMIT :limit` (all values bound; returns `EventTopRow`).
- **Bandwidth** — **New** `bandwidth_timeline(frm, to, bucket)`:
  - Pull per-interface counter samples bucketed; compute deltas reset-safe. Implementation: a SQL query
    that, per (device,label), buckets samples and uses `max(value)-min(value)` per bucket clamped at 0
    as the transferred bytes for that bucket (monotonic counter within a bucket; reset within a bucket is
    rare and clamped). Sum `iface.bytes_in` + `iface.bytes_out` across interfaces per bucket. Returns
    `[(bucket_start, total_bytes)]`. (A more exact lag()-based delta is a later refinement — noted as
    debt.) Bound params throughout; `bucket` bound as a `timedelta`.
  - **New** `bandwidth_totals(frm, to)`: total in / total out over the range (sum of per-interface
    max-min, reset-safe) for the summary line.
- **Availability** — **New** `availability_timeline(frm, to, bucket, device_id)`:
  - `SELECT time_bucket(:bucket, time) AS b, count(*) AS c FROM metrics WHERE tenant_id=:tid AND
     device_id=:did AND metric='cpu.pct' AND time>=:frm AND time<:to GROUP BY b ORDER BY b`. A bucket
    with `c>0` → up (1), absent buckets → down (0). The caller maps to an up/down series across the full
    bucket range.

All additions are tenant-scoped (RLS + explicit `tenant_id`), use bound params, and add no new `field`
interpolation (the only literal remains the 5A allowlisted `bucket` where a str is used; the new
bandwidth/availability queries bind `bucket` as a `timedelta`, the safe path).

## 5. Context + template additions

- **`context.py`** new dataclasses: `WebActivityBlock(timeline_svg, top_sites, top_initiators,
  top_blocked)`, `BandwidthBlock(timeline_svg, total_in, total_out)`, `StatusBlock(timeline_svg,
  uptime_pct)`. Extend `DeviceSection` with optional `web`, `bandwidth`, `status` blocks. Extend
  `build_context` to populate them per device (alongside the existing `attacks`).
- **`template.py` / `report.html.j2`**: after the Attacks block, render (when present) **Web Activity**
  (timeline + Top Sites / Top Initiators / Top Blocked tables), **Data Usage** (timeline + total
  in/out summary), **Up/Down Status** (availability timeline + uptime %). Reuse the `ranked` table +
  `chart` styles. Human-readable byte formatting via a small Jinja filter or pre-formatted strings in the
  context (format in `context.py`, keep the template dumb).
- Byte formatting (`123.4 MB`) done in `context.py` (pure helper) so the template only interpolates
  already-escaped strings.

## 6. Security & safety (inherited + checked)

- Autoescape ON; DNS domains (`name`) are untrusted → rendered as escaped text in tables (never a URL
  attribute). No `Markup`/`| safe` on any data; only the generated SVG (numeric) and CSS stay safe.
- Tenant isolation: all new queries tenant-scoped; an RLS isolation test proves the new sections leak no
  other tenant's DNS/metrics.
- SSRF-safe (inherited 5A `_blocked_fetcher`); no remote resources.
- Bandwidth deltas clamped ≥ 0 (no negative/garbage from counter resets).

## 7. Milestone 5B breakdown (for the plan)
1. **Web Activity aggregation**: `top_blocked_domains` + confirm DNS timeline/top reuse; tests
   (top sites/initiators/blocked, dns timeline) + an RLS isolation test.
2. **Bandwidth aggregation**: `bandwidth_timeline` + `bandwidth_totals` (reset-safe, per-interface
   max-min, summed); tests incl. a **counter-reset** case and a multi-interface case.
3. **Availability aggregation**: `availability_timeline` (poll-presence per bucket) + a helper that maps
   it to an up/down series + uptime %; tests (gap = down).
4. **Context + template + byte-format helper**: new blocks wired into `build_context`; template renders
   Web Activity / Data Usage / Up-Down per device; render tests assert the new sections + data appear and
   no secret/other-tenant data; full-report PDF still valid.
5. **Technical debt** notes.

## 8. Definition of "Done" (5B)
- A generated report shows, per firewall, in addition to Attacks: a **Web Activity** section (DNS
  timeline + Top Sites / Top Initiators / Top Blocked), a **Data Usage** section (transferred-bytes
  timeline + total in/out), and an **Up/Down Status** section (availability timeline + uptime %).
- All from real data, tenant-scoped + RLS-isolated, autoescaped, SSRF-safe; bandwidth reset-safe.
- Backend suite green; no migration.

## 9. Non-goals (5B) / deferred
- **Applications / Web Filter content categories / Top Services / app-id** (needs flow/app-id ingest) — 5C
  (mock + threat-level color coding).
- **Exact lag()-based per-sample bandwidth deltas** — 5B uses per-bucket max-min (reset-clamped); a more
  precise rate is a later refinement.
- **Per-device DNS/bandwidth attribution nuances** (e.g. WAN-only bandwidth, per-initiator nesting) —
  later.
- **White-label config** (5D), **scheduling/storage/history + UI** (5E).

## 10. Open questions (non-blocking)
- **Bandwidth interface selection** — 5B sums all interfaces; a WAN-only view (using interface role) is a
  refinement once interface roles are modeled.
- **Availability granularity** — poll-presence proxy; a dedicated `device.up` metric written each poll
  would make this exact (small 2-side change, deferred).
