# Profile apply: per-interface binding for firewall_rule members — Plan

> REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Let **profile** apply carry an interface binding (one interface for the whole profile application) so a `firewall_rule` member is bound to a real interface instead of always floating. Mirrors the single-template apply binding (PR #51/#52). Other member kinds ignore bindings (their `bind` is None).

**Background:** `materialize_change(..., bindings=)` already binds per-kind (firewall_rule injects `interface`). Single-template apply threads `ApplyTemplateIn.bindings`; profile apply currently does NOT, so firewall_rule profile members apply floating. This adds the same channel to the profile preview + apply.

**Tech:** Python 3.14 / FastAPI; React 19 / Mantine v9 / Vitest. Backend tests need `TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test`; lint `ruff check app/`. Frontend gate: `npx vitest run && npm run lint && npm run build`.

---

### Task 1: Backend — thread `bindings` through profile preview + apply

**Files:** Modify `backend/app/services/profiles.py`, `backend/app/schemas/profiles.py`, `backend/app/api/profiles.py`; Test `backend/tests/test_profiles_api.py` (or the existing profile apply test).

- [ ] **Step 1: Failing test** — add to the profile API tests: a profile containing a `firewall_rule` member, applied with `bindings={"interface":"wan"}`, materializes a `firewall_rule` config_change whose payload has `interface == "wan"` (not floating). And `preview` with the same bindings shows `new.interface == "wan"`. (Seed a `firewall_rule` template + a profile with it as a member; mirror the existing profile-apply test harness.)

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement.**

In `app/schemas/profiles.py`, add `bindings` to `ApplyProfileIn`:
```python
class ApplyProfileIn(BaseModel):
    scheduled_at: datetime | None = None
    bindings: dict = {}
```

In `app/services/profiles.py` `materialize_profile`, accept + thread `bindings`:
```python
async def materialize_profile(
    session: AsyncSession, *, tenant_id: uuid.UUID, device_id: uuid.UUID,
    created_by: uuid.UUID, profile: ConfigProfile, bindings: dict | None = None,
) -> list[ConfigChange]:
    ...
    for tpl, body in effective:
        change = await materialize_change(
            session, tenant_id=tenant_id, device_id=device_id, created_by=created_by,
            template_id=tpl.id, kind=tpl.kind, body=body, bindings=bindings,
        )
        ...
```
(Step 1's per-member `validate_body(tpl.kind, body)` stays on the UN-bound body — firewall_rule validates an empty interface as floating, fine; `materialize_change` binds then re-validates.)

In `app/api/profiles.py`:
- `apply_profile`: pass `bindings=body.bindings` to `materialize_profile`.
- `preview_profile`: accept an optional body and apply bindings to each `eff` before `to_change` so the preview reflects the chosen interface. Reuse `PreviewTemplateIn` (it has `bindings`):
```python
from app.schemas.templates import PreviewTemplateIn  # add to imports
from app.services.templates import apply_bindings      # add to imports

@router.post(".../preview", response_model=list[TemplatePreviewOut], dependencies=[Depends(enforce_csrf)])
async def preview_profile(
    tenant_id: uuid.UUID, device_id: uuid.UUID, profile_id: uuid.UUID,
    body: PreviewTemplateIn | None = None,
    ctx: TenantContext = Depends(require_tenant(Action.CONFIG_PUSH)),
    session: AsyncSession = Depends(get_session),
) -> list[TemplatePreviewOut]:
    ...
    binds = body.bindings if body else {}
    for tpl in templates:
        eff = await _effective(session, tenant_id, tpl)
        eff = apply_bindings(tpl.kind, eff, binds)
        try:
            validate_body(tpl.kind, eff)
        except InvalidTemplateError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        spec = TEMPLATE_KINDS[tpl.kind]
        op, target, _ = spec.to_change(eff)
        previews.append(TemplatePreviewOut(operation=op, kind=spec.change_kind, target=str(target), new=eff))
    return previews
```

- [ ] **Step 4: Run → pass; full backend suite + ruff.**
```bash
cd backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest -q && .venv/bin/ruff check app/
```

- [ ] **Step 5: Commit** `feat(profiles): apply-time interface binding for firewall_rule members`.

**→ Open the backend PR here (or one branch for both; small feature → single PR is fine). Regenerate the frontend API types after the schema changes (`PreviewTemplateIn` body on preview, `bindings` on apply).**

---

### Task 2: Frontend — interface picker in the profile apply UI

**Files:** Modify `frontend/src/profiles/hooks.ts`, `frontend/src/profiles/ApplyProfileSection.tsx`, `frontend/src/i18n/en.ts`; Test `frontend/src/profiles/__tests__/applyProfile.test.tsx`.

- [ ] **Step 0:** `cd frontend && npm run gen:api` (the preview/apply paths gained `bindings`).

- [ ] **Step 1:** `hooks.ts`: `usePreviewProfile` mutationFn → `({ profileId, bindings })` posting `body:{bindings}`; `useApplyProfile` mutationFn → add `bindings`, posting `body:{scheduled_at, bindings}`. Update the `ApplyProfileSection` call sites.

- [ ] **Step 2:** `ApplyProfileSection.tsx`: after a preview, compute `hasFwRule = preview.data?.some(p => p.kind === "firewall_rule")`. When true, render an interface `Select` (testid `prof-apply-interface`) whose options come from `useFirewallRuleModel(deviceId)` `.interfaces` PLUS an empty "floating" option; store the chosen `iface`. Thread `bindings: hasFwRule ? { interface: iface } : {}` into BOTH the (re-)preview and the apply. (Reuse the `useFirewallRuleModel` hook from `src/templates/settingHooks.ts`.) i18n: `templates.profiles.apply.interface` + `floating`.

- [ ] **Step 3: Failing test** — in `applyProfile.test.tsx`: a profile whose preview includes a `firewall_rule` member → `prof-apply-interface` appears; picking "WAN" then applying sends `bindings:{interface:"wan"}` in the apply POST. A profile with no firewall_rule member → no picker, apply sends `bindings:{}`. (Mirror the existing harness; MSW the preview to return a `firewall_rule` kind + the rule-model endpoint for interfaces.)

- [ ] **Step 4:** Run → pass; full frontend gate `npx vitest run && npm run lint && npm run build`.

- [ ] **Step 5: Commit** `feat(profiles): interface picker for firewall_rule members in profile apply`.

---

### Task 3: Docs + verify
- [ ] README: the firewall_rule footnote currently says profile members apply floating — update to note profiles now carry an apply-time interface binding too.
- [ ] Final gates green.

## Self-Review
- bindings flow is identity-preserving for non-firewall kinds (their `bind` is None). The profile applies ONE interface to ALL firewall_rule members (documented; per-member interfaces remain out of scope). Mirrors single-template apply exactly.
