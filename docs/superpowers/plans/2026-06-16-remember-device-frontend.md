# Remember-this-device — PR2 (frontend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Surface the remember-this-device feature in the SPA: a "Trust this device" checkbox at the MFA login step, a per-user "Trusted devices" management section on the security page, and an admin org-toggle — plus the small backend bits those need.

**Architecture:** PR1 shipped the whole backend skip/cookie/management. PR2 adds (a) a backend admin toggle endpoint + a status field so the UI knows whether the feature is on, (b) the typed-client regen, (c) the React UI + hooks mirroring the existing passkeys/sessions patterns, (d) i18n across all 13 locales.

**Tech Stack:** React 19 / TypeScript / Vite / Mantine v9 / openapi-fetch / @tanstack/react-query / msw + vitest. Backend: FastAPI.

**Spec:** `docs/superpowers/specs/2026-06-16-remember-device-design.md`.

**Key existing facts (from exploration):**
- `LoginOut.remember_device = {enabled, days}` already exists on `mfa_required` (PR1).
- Login page: `frontend/src/pages/LoginPage.tsx` — MFA-required handled at the `out.status === "mfa_required"` branch (~line 59); `submitMfa` POSTs `/api/login/mfa` (~line 71); `submitPasskey` POSTs `/api/login/webauthn/complete` (~line 98); MFA-step UI ~lines 156-215.
- Typed client: `frontend/src/api/client.ts` (`api.GET/POST/DELETE`); schema `frontend/src/api/schema.d.ts` regenerated via `npm run gen:api`. DELETE-with-path pattern: `api.DELETE("/api/...{x}", { params: { path: { x } } })` (see `frontend/src/security/mfaHooks.ts`).
- Security page: `frontend/src/security/MfaPanel.tsx` — `PasskeysSection` (list+remove+confirm modal, ~lines 137-269) is the pattern to mirror; superadmin block ~lines 435-443 holds `MfaPolicyControl` (~lines 272-308) — the toggle pattern. Hooks in `frontend/src/security/mfaHooks.ts`; sessions hooks in `frontend/src/security/sessionHooks.ts`.
- Runtime settings auto-render: `frontend/src/admin/RuntimeSettingsSection.tsx` already renders `trusted_device_days` from `/api/admin/settings` (no work needed — label comes from i18n `system.runtime.items.trusted_device_days`).
- i18n: `frontend/src/i18n/en.ts` (source); 12 siblings typed `: Dict` — `it es fr de pt nl ru ar zh zhTW ja` (and `index.ts` loaders). `login.mfa.*` ~lines 36-50; security page `mfa.*` ~lines 463-547; `system.runtime.items` ~line 657.
- Tests: `frontend/src/pages/__tests__/loginMfa.test.tsx` (msw + testid pattern); `frontend/src/security/__tests__/mfaPanel.test.tsx`.
- Backend status endpoint: `backend/app/api/mfa.py` `mfa_status` returns `MfaStatusOut` (`backend/app/schemas/mfa.py`); admin toggle pattern = `mfa_policy_get/set` (`/api/admin/mfa-policy`) using `get/set_mfa_policy`. `get/set_trusted_device_enabled` already exist in `app_settings.py`.

**Commands:**
- Backend tests (env per PR1 plan): `cd backend && python -m pytest tests/<f> -q`
- Regen client: `cd frontend && npm run gen:api`
- Frontend gate: `cd frontend && npm run build` (tsc -b + vite build — the CI gate), `npm test`, `npm run lint`.
- i18n parity is compiler-enforced: adding a key to `en.ts` breaks the build until mirrored in all 12 siblings.

---

### Task 1: Backend — admin toggle endpoint + status exposure

**Files:**
- Modify: `backend/app/schemas/mfa.py` (add `TrustedDeviceFeature`, extend `MfaStatusOut`; add `TrustedDeviceToggleIn/Out`)
- Modify: `backend/app/api/mfa.py` (`mfa_status`: include the toggle state; add `GET/PUT /api/admin/trusted-device-enabled`)
- Test: `backend/tests/test_trusted_device_toggle_api.py`

- [ ] **Step 1: failing test** — `backend/tests/test_trusted_device_toggle_api.py`:

```python
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.app_settings import get_trusted_device_enabled
from tests.conftest import csrf_headers
from tests.factories import make_user


async def _superadmin(api_client, db_engine, email="adm@x.io"):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await make_user(s, email=email, password="pw12345-secure", is_superadmin=True)
        await s.commit()
    await api_client.post("/api/login", json={"email": email, "password": "pw12345-secure"})


async def test_get_and_set_toggle(api_client, db_engine):
    await _superadmin(api_client, db_engine)
    r = await api_client.get("/api/admin/trusted-device-enabled")
    assert r.status_code == 200 and r.json()["enabled"] is True  # default on
    h = csrf_headers(api_client)
    r = await api_client.put("/api/admin/trusted-device-enabled", json={"enabled": False}, headers=h)
    assert r.status_code == 200 and r.json()["enabled"] is False
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        assert await get_trusted_device_enabled(s, env_default=True) is False


async def test_toggle_requires_superadmin(api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await make_user(s, email="plain@x.io", password="pw12345-secure")
        await s.commit()
    await api_client.post("/api/login", json={"email": "plain@x.io", "password": "pw12345-secure"})
    assert (await api_client.get("/api/admin/trusted-device-enabled")).status_code == 403


async def test_status_includes_trusted_device_feature(api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await make_user(s, email="u@x.io", password="pw12345-secure")
        await s.commit()
    await api_client.post("/api/login", json={"email": "u@x.io", "password": "pw12345-secure"})
    r = await api_client.get("/api/me/mfa")
    assert r.status_code == 200
    assert r.json()["trusted_devices"]["enabled"] is True
```

- [ ] **Step 2: run → fail** (`ImportError`/404/KeyError).

- [ ] **Step 3: schemas** — in `backend/app/schemas/mfa.py` add:

```python
class TrustedDeviceFeature(BaseModel):
    enabled: bool  # org-wide remember-this-device toggle


class TrustedDeviceToggleIn(BaseModel):
    enabled: bool


class TrustedDeviceToggleOut(BaseModel):
    enabled: bool
```

and extend `MfaStatusOut` with: `trusted_devices: TrustedDeviceFeature`.

- [ ] **Step 4: status** — in `backend/app/api/mfa.py` `mfa_status`, read the toggle and include it:

```python
from app.core.config import get_settings
from app.services.app_settings import get_trusted_device_enabled
# ...
    td_enabled = await get_trusted_device_enabled(
        session, env_default=get_settings().trusted_device_enabled
    )
    return MfaStatusOut(
        enabled=bool(row and row.enabled),
        recovery_codes_remaining=int(remaining),
        webauthn=WebAuthnStatus(configured=cfg.is_configured(), credentials=int(cred_count)),
        trusted_devices=TrustedDeviceFeature(enabled=td_enabled),
    )
```

- [ ] **Step 5: admin endpoints** — in `backend/app/api/mfa.py` add (mirroring `mfa_policy_get/set`):

```python
@router.get("/admin/trusted-device-enabled", response_model=TrustedDeviceToggleOut)
async def trusted_device_toggle_get(
    user: User = Depends(require_org(Action.USER_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> TrustedDeviceToggleOut:
    enabled = await get_trusted_device_enabled(session, env_default=get_settings().trusted_device_enabled)
    return TrustedDeviceToggleOut(enabled=enabled)


@router.put(
    "/admin/trusted-device-enabled",
    response_model=TrustedDeviceToggleOut,
    dependencies=[Depends(enforce_csrf)],
)
async def trusted_device_toggle_set(
    body: TrustedDeviceToggleIn,
    user: User = Depends(require_org(Action.USER_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> TrustedDeviceToggleOut:
    await set_trusted_device_enabled(session, body.enabled)
    await AuditService(session).record(
        actor_user_id=user.id, tenant_id=None, action="auth.trusted_device.policy_change",
        target_type="app_settings", target_id="trusted_device_enabled", ip=None,
        details={"enabled": body.enabled},
    )
    await session.commit()
    return TrustedDeviceToggleOut(enabled=body.enabled)
```

Add imports: `from app.services.app_settings import ... set_trusted_device_enabled` and the new schema names.

- [ ] **Step 6: run → pass**; `pytest tests/test_trusted_device_toggle_api.py tests/test_audit_coverage.py tests/test_mfa_enroll_api.py -q`; `ruff check app/`.

- [ ] **Step 7: commit** `feat(mfa): trusted-device admin toggle endpoint + status exposure`.

---

### Task 2: Regenerate the typed API client

**Files:** `frontend/src/api/schema.d.ts` (generated)

- [ ] **Step 1:** with the backend importable, `cd frontend && npm run gen:api`.
- [ ] **Step 2:** confirm the diff includes `remember_device` on the login response, `/api/me/trusted-devices` (GET + DELETE), `/api/me/trusted-devices/{device_id}` (DELETE), `/api/admin/trusted-device-enabled` (GET/PUT), and `trusted_devices` on the `/api/me/mfa` response. `npx tsc -b` (or rely on Task gates).
- [ ] **Step 3: commit** `chore(api): regenerate client for trusted-device endpoints`.

---

### Task 3: Trusted-devices hooks

**Files:**
- Create: `frontend/src/security/trustedDeviceHooks.ts`
- Test: covered via Task 5's panel test.

- [ ] **Step 1: implement** (mirror `sessionHooks.ts` + `mfaHooks.ts`):

```typescript
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { components } from "../api/schema";

export type TrustedDevice = components["schemas"]["TrustedDeviceOut"];

export function useTrustedDevices(enabled: boolean) {
  return useQuery({
    queryKey: ["trusted-devices"],
    enabled,
    queryFn: async (): Promise<TrustedDevice[]> => {
      const { data, error } = await api.GET("/api/me/trusted-devices");
      if (error || !data) throw new Error("Failed to load trusted devices");
      return data;
    },
  });
}

export function useRevokeTrustedDevice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string): Promise<void> => {
      const { error } = await api.DELETE("/api/me/trusted-devices/{device_id}", {
        params: { path: { device_id: id } },
      });
      if (error) throw new Error("Could not revoke the device");
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["trusted-devices"] }),
  });
}

export function useRevokeAllTrustedDevices() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<void> => {
      const { error } = await api.DELETE("/api/me/trusted-devices");
      if (error) throw new Error("Could not revoke trusted devices");
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["trusted-devices"] }),
  });
}
```

- [ ] **Step 2: commit** `feat(mfa): trusted-device react-query hooks`.

---

### Task 4: Login — "Trust this device" checkbox

**Files:**
- Modify: `frontend/src/pages/LoginPage.tsx`
- Modify: `frontend/src/i18n/en.ts` (+ 12 locales)
- Test: `frontend/src/pages/__tests__/loginMfa.test.tsx`

- [ ] **Step 1: i18n** — add to `login.mfa` in `en.ts`:

```typescript
rememberDevice: "Trust this device for {days} days",
rememberDeviceHelp: "You won't be asked for a second factor next time you sign in from this device.",
```

Mirror both keys in all 12 sibling locales (`it es fr de pt nl ru ar zh zhTW ja`) — translate; keep `{days}` placeholder verbatim.

- [ ] **Step 2: failing test** — add to `loginMfa.test.tsx` a case: `POST /api/login` returns `{ status: "mfa_required", remember_device: { enabled: true, days: 30 } }`; assert the checkbox (testid `mfa-remember-device`) appears with "30" in its label; check it; submit the TOTP code; assert the captured `/api/login/mfa` body equals `{ code: "123456", remember_device: true }`. Add a second assertion that when `remember_device.enabled` is false (or absent) the checkbox is NOT rendered.

- [ ] **Step 3: implement** in `LoginPage.tsx`:
  - Add state: `const [rememberDevice, setRememberDevice] = useState(false);` and `const [remember, setRemember] = useState<{enabled: boolean; days: number} | null>(null);`
  - In the `out.status === "mfa_required"` branch, set `setRemember(out.remember_device ?? null);`
  - In `submitMfa`, send `body: { code: values.code, ...(remember?.enabled ? { remember_device: rememberDevice } : {}) }`.
  - In `submitPasskey`, send `body: { credential, ...(remember?.enabled ? { remember_device: rememberDevice } : {}) }`.
  - In the MFA-step UI (after the passkey button, before the error `<Text>`), render when `remember?.enabled`:

```tsx
{remember?.enabled && (
  <Checkbox
    mt="sm"
    data-testid="mfa-remember-device"
    label={t.login.mfa.rememberDevice.replace("{days}", String(remember.days))}
    description={t.login.mfa.rememberDeviceHelp}
    checked={rememberDevice}
    onChange={(e) => setRememberDevice(e.currentTarget.checked)}
  />
)}
```

  - Import `Checkbox` from `@mantine/core`.

- [ ] **Step 4:** `npm test -- loginMfa`; `npm run build`.
- [ ] **Step 5: commit** `feat(mfa): trust-this-device checkbox at the MFA login step`.

---

### Task 5: Security page — Trusted devices section

**Files:**
- Create: `frontend/src/security/TrustedDevicesSection.tsx`
- Modify: `frontend/src/security/MfaPanel.tsx` (render the section, gated on status)
- Modify: `frontend/src/i18n/en.ts` (+ 12 locales) — `mfa.trustedDevices.*`
- Test: `frontend/src/security/__tests__/mfaPanel.test.tsx`

- [ ] **Step 1: i18n** — add a `trustedDevices` block under the security-page `mfa` namespace in `en.ts` (title, intro, column labels device/ip/created/lastUsed/expires, revoke, revokeAll, confirmTitle, confirmBody, empty, loadError, revokeError, neverUsed). Mirror in all 12 locales.

- [ ] **Step 2: failing test** — in `mfaPanel.test.tsx`: mock `/api/me/mfa` to include `trusted_devices: { enabled: true }`, mock `GET /api/me/trusted-devices` → one device, mock `DELETE /api/me/trusted-devices/d1`. Assert the section renders the device row; click its revoke (testid `trusted-device-revoke-d1`), confirm, assert the row disappears (query invalidation refetches → empty). Add a case: `trusted_devices.enabled === false` → the section is NOT rendered.

- [ ] **Step 3: implement** `TrustedDevicesSection.tsx` (mirror `PasskeysSection` structure: Card title + intro, a Mantine `Table` of devices, a Remove button per row opening a confirm modal, an error alert, an empty state, and a "Revoke all" button). Use `useTrustedDevices(true)`, `useRevokeTrustedDevice()`, `useRevokeAllTrustedDevices()`. Format timestamps with the existing date util used by `SessionsPage.tsx`. Use `useT()` for all strings.

- [ ] **Step 4: wire** in `MfaPanel.tsx` — after the passkeys card, render gated on the status:

```tsx
{statusQuery.data?.trusted_devices?.enabled && (
  <Card withBorder padding="lg" radius="md">
    <TrustedDevicesSection />
  </Card>
)}
```

- [ ] **Step 5:** `npm test -- mfaPanel`; `npm run build`.
- [ ] **Step 6: commit** `feat(mfa): trusted-devices management section on the security page`.

---

### Task 6: Admin org toggle UI

**Files:**
- Modify: `frontend/src/security/mfaHooks.ts` (add `useTrustedDeviceToggle` GET + `useSetTrustedDeviceToggle` PUT)
- Modify: `frontend/src/security/MfaPanel.tsx` (a Switch in the superadmin block, next to `MfaPolicyControl`)
- Modify: `frontend/src/i18n/en.ts` (+ 12 locales) — `mfa.trustedDeviceToggle.*`
- Test: extend `mfaPanel.test.tsx`

- [ ] **Step 1: hooks** — mirror `useMfaPolicy`/`useSetMfaPolicy` against `/api/admin/trusted-device-enabled` (GET → `{enabled}`, PUT body `{enabled}`); invalidate `["trusted-device-toggle"]`.

- [ ] **Step 2: i18n** — `trustedDeviceToggle: { label, help, on, off }` in `en.ts` + 12 locales.

- [ ] **Step 3: failing test** — in `mfaPanel.test.tsx` (superadmin context): mock `GET /api/admin/trusted-device-enabled` → `{enabled:true}` and `PUT` capturing the body; assert the Switch renders checked; toggle it; assert the PUT body `{enabled:false}`.

- [ ] **Step 4: implement** a `TrustedDeviceToggleControl` (Mantine `Switch`) in the superadmin section of `MfaPanel.tsx`, wired to the hooks, with `t.mfa.trustedDeviceToggle.*` strings.

- [ ] **Step 5:** `npm test -- mfaPanel`; `npm run build`.
- [ ] **Step 6: commit** `feat(mfa): admin org toggle for remember-this-device`.

---

### Task 7: i18n parity + runtime-setting label + gate

**Files:**
- Modify: `frontend/src/i18n/en.ts` (+ 12 locales) — `system.runtime.items.trusted_device_days`
- Verify: full build/test/lint

- [ ] **Step 1:** add `trusted_device_days: { label: "Trusted device lifetime (days)", help: "How long a trusted device skips the second factor." }` under `system.runtime.items` in `en.ts`; mirror in all 12 locales.
- [ ] **Step 2: full gate** — `cd frontend && npm run build && npm test && npm run lint`. The build (`tsc -b`) fails if any locale is missing any key — fix until green.
- [ ] **Step 3: backend gate** — `cd backend && python -m pytest tests/test_trusted_device_toggle_api.py tests/test_audit_coverage.py -q && ruff check app/`.
- [ ] **Step 4: commit** `feat(i18n): runtime-setting label + locale parity for trusted devices`.

---

### Task 8: Finalize milestone

- [ ] CHANGELOG.md → `[0.21.0]` entry (remember-this-device, backend PR1 + frontend PR2).
- [ ] README.md → mention remember-this-device under security/MFA.
- [ ] Wiki (Security page) → document the feature + the admin toggle + revocation.
- [ ] Refresh screenshots if the login/security UI changed materially (Playwright capture procedure).
- [ ] Tag `v0.21.0` after PR2 merges green.

## Self-review notes
- Spec coverage: checkbox (T4), management section (T5), admin toggle (T1 backend + T6 UI), status exposure for section visibility (T1), runtime day label (T7), client regen (T2), hooks (T3). All spec UI items mapped.
- Type consistency: `TrustedDeviceFeature.enabled` (T1) consumed as `status.trusted_devices.enabled` (T5/T4-status); `remember_device.{enabled,days}` (PR1) consumed in T4; hook names `useTrustedDevices/useRevokeTrustedDevice/useRevokeAllTrustedDevices` (T3) used in T5.
- i18n: every new `en.ts` key (T4/T5/T6/T7) must be mirrored in all 12 siblings or `npm run build` fails — that's the enforced gate.
