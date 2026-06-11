# monit_test auto-attach to the system service — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Opt-in `attach_to_system` on `monit_test` — when set, applying the template also attaches the test to the device's Monit `system` service (so it takes effect). Default off.

**Tech:** Python 3.14 / FastAPI / httpx / respx; React 19 / Mantine v9 / Vitest. venv `/home/l0rdg3x/coding/OPNGMS/backend/.venv/bin/python`; tests need `TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test`; CI lint `ruff check app/`. Frontend gate: `npx vitest run && npm run lint && npm run build` (build = tsc -b + vite build; required — tsc -b checks tests).

**Verified API (real box, revertible):** system service = `searchService` row with `type=="system"`; attach = `getService/{sid}` → add the test uuid to its selected `tests` → `setService/{sid} {"service":{"tests":"<comma-joined>"}}` (partial merge) → `monit/service/reconfigure`.

---

### Task 1: Connector — attach logic in `apply_monit_test`

**Files:** Modify `backend/app/connectors/opnsense/client.py`; Test `backend/tests/test_monit_connector.py`

- [ ] **Step 1: Add failing tests** to `backend/tests/test_monit_connector.py`:

```python
@respx.mock
async def test_apply_monit_test_attaches_to_system_service():
    # upsert (add) then attach to the system service
    respx.get(url__regex=r".*/api/monit/settings/searchTest.*").mock(return_value=httpx.Response(200, json={"rows": []}))
    respx.post(url__regex=r".*/api/monit/settings/searchTest.*").mock(return_value=httpx.Response(200, json={"rows": []}))
    add = respx.post(url__regex=r".*/api/monit/settings/addTest.*").mock(
        return_value=httpx.Response(200, json={"result": "saved", "uuid": "T1"}))
    respx.post(url__regex=r".*/api/monit/settings/searchService.*").mock(
        return_value=httpx.Response(200, json={"rows": [{"uuid": "SYS", "type": "system"}, {"uuid": "X", "type": "custom"}]}))
    respx.get(url__regex=r".*/api/monit/settings/getService/SYS.*").mock(
        return_value=httpx.Response(200, json={"service": {"tests": {"OLD": {"value": "Old", "selected": 1}, "T1": {"value": "New", "selected": 0}}}}))
    captured = {}
    def _set(request):
        import json as _j; captured.update(_j.loads(request.content)); return httpx.Response(200, json={"result": "saved"})
    respx.post(url__regex=r".*/api/monit/settings/setService/SYS.*").mock(side_effect=_set)
    respx.post(url__regex=r".*/api/monit/service/reconfigure.*").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    res = await _c().apply_monit_test("set", {"name": "CPUHigh", "type": "SystemResource", "action": "alert", "attach_to_system": "1"}, dry_run=False)
    assert add.called and res["attached"] is True
    # the attach merged the new uuid into the service's existing tests, and the sent test payload had NO attach flag
    assert set(captured["service"]["tests"].split(",")) == {"OLD", "T1"}
    import json as _j
    sent_test = _j.loads(add.calls[0].request.content)["test"]
    assert "attach_to_system" not in sent_test


@respx.mock
async def test_apply_monit_test_no_attach_when_flag_off():
    respx.post(url__regex=r".*/api/monit/settings/searchTest.*").mock(return_value=httpx.Response(200, json={"rows": []}))
    respx.post(url__regex=r".*/api/monit/settings/addTest.*").mock(return_value=httpx.Response(200, json={"result": "saved", "uuid": "T1"}))
    svc = respx.post(url__regex=r".*/api/monit/settings/searchService.*")
    respx.post(url__regex=r".*/api/monit/service/reconfigure.*").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    res = await _c().apply_monit_test("set", {"name": "CPUHigh", "type": "SystemResource", "action": "alert"}, dry_run=False)
    assert not svc.called and res["attached"] is False


@respx.mock
async def test_attach_refuses_ambiguous_system_service():
    respx.post(url__regex=r".*/api/monit/settings/searchTest.*").mock(return_value=httpx.Response(200, json={"rows": []}))
    respx.post(url__regex=r".*/api/monit/settings/addTest.*").mock(return_value=httpx.Response(200, json={"result": "saved", "uuid": "T1"}))
    respx.post(url__regex=r".*/api/monit/settings/searchService.*").mock(
        return_value=httpx.Response(200, json={"rows": [{"uuid": "A", "type": "system"}, {"uuid": "B", "type": "system"}]}))
    with pytest.raises(ApiError):
        await _c().apply_monit_test("set", {"name": "X", "type": "SystemResource", "action": "alert", "attach_to_system": "1"}, dry_run=False)
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** — replace `apply_monit_test` and add two helpers in `client.py`:

```python
    async def apply_monit_test(self, operation: str, payload: dict, *, dry_run: bool = True) -> dict:
        """Upsert a Monit test by `name`; optionally attach it to the system service; then reconfigure.

        `attach_to_system` ("1") is a directive, stripped from the test payload before it is sent.
        Identity is `name` (1 match -> setTest; none -> addTest; many -> refuse). dry_run mutates nothing."""
        payload = dict(payload)
        attach = str(payload.pop("attach_to_system", "0")) in ("1", "true", "True")
        name = str(payload.get("name", ""))
        if dry_run:
            return {"dry_run": True, "name": name, "attach_to_system": attach}
        uuid_ = await self._resolve_monit_test_uuid(name)
        if uuid_ is None:
            res = await self._post("monit/settings/addTest", {"test": payload})
            test_uuid, op = res.get("uuid"), "add"
        else:
            res = await self._post(f"monit/settings/setTest/{uuid_}", {"test": payload})
            test_uuid, op = uuid_, "set"
        if attach and test_uuid:
            await self._attach_test_to_system(test_uuid)
        await self._post("monit/service/reconfigure", {}, timeout=RECONFIGURE_TIMEOUT)
        return {"dry_run": False, "operation": op, "attached": bool(attach), "result": res}

    async def _resolve_system_service_uuid(self) -> str:
        """The Monit `system`-type service uuid. Refuse (ApiError) if zero or >1 (never mutate on doubt)."""
        data = await self._post("monit/settings/searchService", {"current": 1, "rowCount": 1000})
        matches = [r for r in data.get("rows", []) if str(r.get("type", "")).lower() == "system"]
        if len(matches) != 1:
            raise ApiError(0, f"monit system service not uniquely resolvable ({len(matches)})")
        return matches[0]["uuid"]

    async def _attach_test_to_system(self, test_uuid: str) -> None:
        """Add `test_uuid` to the system service's tests (partial merge). Idempotent."""
        sid = await self._resolve_system_service_uuid()
        svc = (await self._get(f"monit/settings/getService/{sid}")).get("service", {})
        tests = svc.get("tests", {})
        selected = [k for k, v in tests.items() if isinstance(v, dict) and str(v.get("selected")) in ("1", "True")]
        if test_uuid in selected:
            return
        selected.append(test_uuid)
        await self._post(f"monit/settings/setService/{sid}", {"service": {"tests": ",".join(selected)}})
```

- [ ] **Step 4: Run → pass.**

- [ ] **Step 5: Commit**

```bash
cd backend && .venv/bin/ruff check app/connectors/opnsense/client.py
git add app/connectors/opnsense/client.py tests/test_monit_connector.py
git commit -m "feat(monit): optional attach of a monit_test to the system service"
```

---

### Task 2: Kind validator accepts/validates the flag

**Files:** Modify `backend/app/services/monit_kind.py`; Test `backend/tests/test_monit_kind.py`

- [ ] **Step 1: Failing tests** — add to `tests/test_monit_kind.py`:

```python
def test_validate_accepts_attach_flag():
    tpl.validate_body("monit_test", {**_GOOD, "attach_to_system": "1"})
    tpl.validate_body("monit_test", {**_GOOD, "attach_to_system": "0"})


def test_validate_rejects_bad_attach_flag():
    with pytest.raises(tpl.InvalidTemplateError):
        tpl.validate_body("monit_test", {**_GOOD, "attach_to_system": "yes"})
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** — in `app/services/monit_kind.py` `_validate`, append:

```python
    av = body.get("attach_to_system")
    if av is not None and str(av) not in ("0", "1"):
        raise InvalidTemplateError("monit test 'attach_to_system' must be '0' or '1'")
```

- [ ] **Step 4: Run → pass; full backend suite + ruff.**

```bash
cd backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest -q && .venv/bin/ruff check app/
```

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/monit_kind.py tests/test_monit_kind.py
git commit -m "feat(monit): validate the attach_to_system flag on the kind"
```

**→ Open the BACKEND part of the PR here (or keep one branch for both backend+frontend; this feature is small — a single PR is fine).**

---

### Task 3: Frontend — "Attach to system service" checkbox

**Files:** Modify `frontend/src/templates/MonitTestForm.tsx`, `frontend/src/i18n/en.ts`; Test `frontend/src/security/.../monitTestForm.test.tsx` (the existing monit form test under `src/templates/__tests__/`).

- [ ] **Step 1:** Read `src/templates/MonitTestForm.tsx`. It renders the introspection auto-form over `value.payload`. Add a Mantine `Checkbox` (testid `monit-attach-system`) BELOW the auto-form, checked when `value.payload.attach_to_system === "1"`, toggling it via `onChange({ payload: { ...value.payload, attach_to_system: checked ? "1" : "0" } })`. Add an i18n string `templates.monit.attachSystem` ("Attach this test to the system service so it takes effect") + a short helper note.

- [ ] **Step 2: Failing test** — extend `src/templates/__tests__/monitTestForm.test.tsx`: after load, toggling `monit-attach-system` sets `latest.payload.attach_to_system` to `"1"`.

- [ ] **Step 3:** Implement, run → pass.

- [ ] **Step 4:** Full frontend gate: `cd frontend && npx vitest run && npm run lint && npm run build` — all green.

- [ ] **Step 5: Commit**

```bash
cd frontend && git add src/templates/MonitTestForm.tsx src/templates/__tests__/monitTestForm.test.tsx src/i18n/en.ts
git commit -m "feat(monit): attach-to-system checkbox in the monit_test form"
```

---

### Task 4: Live verify + docs

- [ ] **Live verify (revertible)** via an ephemeral `/tmp` connector probe against the box: apply a `monit_test` with `attach_to_system="1"`; confirm the test exists AND is selected in the system service's `tests`; then detach (restore the service's original tests) + delTest + reconfigure.
- [ ] **README:** the monit_test mention can note the optional attach-to-system. (Light touch; the templates footnote already covers monit_test.)
- [ ] Final gates green (backend `pytest -q` + `ruff check app/`; frontend `vitest` + `lint` + `build`).

## Self-Review notes
- `attach_to_system` is a directive, stripped from the test payload (never sent to addTest/setTest). Idempotent attach (no duplicate). System service resolved by `type=="system"`, refuses ambiguity. Default off → no behavior change for existing templates.
