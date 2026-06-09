# OPNGMS Fase 1 · Milestone D — Shell Frontend React (login + app shell + device) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dare a OPNGMS una console web React: login a sessione, app shell con tenant switcher, e la UI completa di gestione device (lista/dettaglio, onboarding, test-connection, rotate-secret, edit, delete) — il flusso a più alto valore (gestire i firewall dei clienti), cablata sull'API backend.

**Architecture:** SPA Vite + React + TypeScript con **Mantine** (AppShell/Table/form/Modal/Notifications). Routing con **React Router**. Stato server con **TanStack Query**. Client API **tipizzato generato da OpenAPI** (`openapi-typescript` → `schema.d.ts`, `openapi-fetch` con `credentials: 'include'` e un middleware che aggiunge l'header `X-OPNGMS-CSRF` su ogni mutazione). L'auth è basata su cookie httpOnly: il frontend NON legge il cookie, deriva lo stato da `GET /api/me` (200=loggato, 401=no). Test con **Vitest + React Testing Library + MSW** (mock dell'API a livello di rete). Il frontend vive in `frontend/`, sibling di `backend/`.

**Tech Stack:** Node 26 / npm 11, Vite, React 18, TypeScript, Mantine v7, @tanstack/react-query v5, react-router-dom v6, openapi-fetch + openapi-typescript, Vitest, @testing-library/react, msw.

---

## Riferimento spec / decisioni
Implementa la sez. 15 dello spec (shell frontend). Decisioni di pianificazione: **Mantine**; scope
**focalizzato** (login + shell + device UI; org-admin UI = follow-up); client API generato da
OpenAPI; auth via `/api/me`. Backend (Milestone A-C) in `main` con API device complete.

## Prerequisiti
- Backend funzionante con endpoint: `POST /api/login`, `POST /api/logout`, `GET /api/me`,
  `GET/POST/PATCH/DELETE /api/tenants/{tid}/devices` + `/test-connection` + `/rotate-secret`,
  `GET /api/tenants` (superadmin). Cookie `opngms_session` httpOnly/secure/SameSite=Lax; CSRF via
  header `X-OPNGMS-CSRF` sulle mutazioni.
- Node 26 + npm 11 disponibili; backend venv in `backend/.venv` (serve solo per esportare lo schema OpenAPI).

## Struttura file (creati in questa milestone)
```
backend/
  app/api/me_tenants.py        # NEW (Task 1): GET /api/me/tenants
  app/repositories/membership.py  # MODIFY: list_for_user
  app/main.py                  # MODIFY: mount router
  tests/test_me_tenants.py     # NEW
  scripts/export_openapi.py    # NEW (Task 2): dump schema OpenAPI
frontend/
  package.json  index.html  vite.config.ts  tsconfig.json  .gitignore
  openapi.json                 # generato
  src/
    main.tsx  App.tsx  theme.ts
    api/
      client.ts                # openapi-fetch + credentials + CSRF middleware
      schema.d.ts              # generato da openapi-typescript
    auth/
      AuthProvider.tsx  useAuth.ts  ProtectedRoute.tsx
    pages/
      LoginPage.tsx
      DevicesPage.tsx
      DeviceDetailPage.tsx
    components/
      AppShell.tsx  TenantSwitcher.tsx  DeviceCreateModal.tsx  DeviceActions.tsx
    tenant/TenantProvider.tsx  useTenant.ts
    test/setup.ts  server.ts  utils.tsx
  src/**/__tests__/*.test.tsx
```

---

## Task 1 (backend): `GET /api/me/tenants`

Il tenant switcher deve elencare i tenant accessibili all'utente corrente. Aggiunta API minima.

**Files:** Modify `backend/app/repositories/membership.py`, `backend/app/main.py`; Create `backend/app/api/me_tenants.py`, `backend/tests/test_me_tenants.py`

- [ ] **Step 1: Failing test** — `backend/tests/test_me_tenants.py`:
```python
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.factories import make_membership, make_tenant, make_user


async def test_member_sees_only_their_tenants(api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        a = await make_tenant(s, slug="a", name="Alpha")
        await make_tenant(s, slug="b", name="Beta")  # non membro
        u = await make_user(s, email="u@x.io", password="pw12345")
        await make_membership(s, user_id=u.id, tenant_id=a.id, role="operator")
        await s.commit()
    await api_client.post("/api/login", json={"email": "u@x.io", "password": "pw12345"})
    resp = await api_client.get("/api/me/tenants")
    assert resp.status_code == 200
    body = resp.json()
    assert [t["slug"] for t in body] == ["a"]
    assert body[0]["role"] == "operator"


async def test_superadmin_sees_all_tenants(api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await make_tenant(s, slug="a")
        await make_tenant(s, slug="b")
        await s.commit()
    await api_client.post(
        "/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345"}
    )
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345"})
    resp = await api_client.get("/api/me/tenants")
    assert resp.status_code == 200
    assert {t["slug"] for t in resp.json()} == {"a", "b"}


async def test_me_tenants_requires_auth(api_client):
    assert (await api_client.get("/api/me/tenants")).status_code == 401
```
Run: `cd backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest tests/test_me_tenants.py -v` → FAIL (404).

- [ ] **Step 2: Add `list_for_user`** to `backend/app/repositories/membership.py` (keep existing methods; `select` imported):
```python
    async def list_for_user(self, user_id: uuid.UUID):
        result = await self.session.execute(
            select(Membership).where(Membership.user_id == user_id)
        )
        return list(result.scalars().all())
```

- [ ] **Step 3: Router** — `backend/app/api/me_tenants.py`:
```python
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import get_current_user
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.membership import MembershipRepository

router = APIRouter(prefix="/api/me", tags=["me"])


class MyTenantOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    role: str | None  # None per superadmin (accesso globale)


@router.get("/tenants", response_model=list[MyTenantOut])
async def my_tenants(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[MyTenantOut]:
    if user.is_superadmin:
        tenants = (
            (await session.execute(select(Tenant).order_by(Tenant.slug))).scalars().all()
        )
        return [
            MyTenantOut(id=t.id, name=t.name, slug=t.slug, role=None) for t in tenants
        ]
    memberships = await MembershipRepository(session).list_for_user(user.id)
    by_id: dict[uuid.UUID, str] = {m.tenant_id: m.role for m in memberships}
    if not by_id:
        return []
    tenants = (
        (await session.execute(select(Tenant).where(Tenant.id.in_(by_id.keys())).order_by(Tenant.slug)))
        .scalars()
        .all()
    )
    return [
        MyTenantOut(id=t.id, name=t.name, slug=t.slug, role=by_id[t.id]) for t in tenants
    ]
```

- [ ] **Step 4: Mount** in `backend/app/main.py`: `from app.api.me_tenants import router as me_tenants_router` + `app.include_router(me_tenants_router)`.

- [ ] **Step 5: Run + commit**
```bash
cd backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest tests/test_me_tenants.py -q
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest -q
git add backend/app/api/me_tenants.py backend/app/repositories/membership.py backend/app/main.py backend/tests/test_me_tenants.py
git commit -m "feat(backend): GET /api/me/tenants (tenant accessibili dall'utente)"
```
Expected: 3 pass; full suite green (91 passed).

---

## Task 2: Scaffold frontend + client API + harness di test

**Files:** create `frontend/` project. `backend/scripts/export_openapi.py`.

- [ ] **Step 1: Scaffold Vite + deps**
```bash
cd /home/l0rdg3x/coding/OPNGMS
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
npm install @mantine/core @mantine/hooks @mantine/form @mantine/notifications @tanstack/react-query react-router-dom openapi-fetch
npm install -D openapi-typescript vitest @testing-library/react @testing-library/user-event @testing-library/jest-dom jsdom msw @vitest/coverage-v8
```
Add to `frontend/.gitignore` (Vite template includes most): ensure `node_modules`, `dist`, `coverage` are ignored. Do NOT gitignore `openapi.json`/`src/api/schema.d.ts` (commit the generated client).

- [ ] **Step 2: OpenAPI export script** — `backend/scripts/export_openapi.py`:
```python
import json
import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://opngms:opngms@localhost:5432/opngms")
os.environ.setdefault("SESSION_SECRET", "export")
os.environ.setdefault("MASTER_KEY", "export-placeholder")  # non usato da openapi()

from app.main import app  # noqa: E402

print(json.dumps(app.openapi(), indent=2))
```
Generate the schema + types:
```bash
cd /home/l0rdg3x/coding/OPNGMS/backend && .venv/bin/python scripts/export_openapi.py > ../frontend/openapi.json
cd /home/l0rdg3x/coding/OPNGMS/frontend && npx openapi-typescript openapi.json -o src/api/schema.d.ts
```
Add an npm script to `frontend/package.json`: `"gen:api": "cd ../backend && .venv/bin/python scripts/export_openapi.py > ../frontend/openapi.json && cd ../frontend && openapi-typescript openapi.json -o src/api/schema.d.ts"`.

- [ ] **Step 3: Typed API client** — `frontend/src/api/client.ts`:
```typescript
import createClient, { type Middleware } from "openapi-fetch";
import type { paths } from "./schema";

const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);

const csrfMiddleware: Middleware = {
  onRequest({ request }) {
    if (MUTATING.has(request.method.toUpperCase())) {
      request.headers.set("X-OPNGMS-CSRF", "1");
    }
    return request;
  },
};

export const api = createClient<paths>({
  baseUrl: import.meta.env.VITE_API_BASE ?? "",
  credentials: "include", // invia il cookie di sessione httpOnly
});
api.use(csrfMiddleware);
```
(`baseUrl` empty → same-origin; in dev usa il proxy Vite verso il backend.)

- [ ] **Step 4: Vite dev proxy + config** — `frontend/vite.config.ts`:
```typescript
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": "http://localhost:8000" },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
```

- [ ] **Step 5: Test harness** — `frontend/src/test/setup.ts`:
```typescript
import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll } from "vitest";
import { server } from "./server";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
```
`frontend/src/test/server.ts`:
```typescript
import { setupServer } from "msw/node";

export const server = setupServer();
```
`frontend/src/test/utils.tsx`:
```typescript
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

export function renderWithProviders(ui: ReactElement, { route = "/" } = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <MantineProvider>
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={[route]}>{children}</MemoryRouter>
        </QueryClientProvider>
      </MantineProvider>
    );
  }
  return render(ui, { wrapper: Wrapper });
}
```

- [ ] **Step 6: Smoke test** — `frontend/src/App.tsx`:
```typescript
export default function App() {
  return <div>OPNGMS</div>;
}
```
`frontend/src/__tests__/App.test.tsx`:
```typescript
import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "../App";
import { renderWithProviders } from "../test/utils";

describe("App", () => {
  it("renders the app name", () => {
    renderWithProviders(<App />);
    expect(screen.getByText("OPNGMS")).toBeInTheDocument();
  });
});
```
Add `"test": "vitest run"` to package.json scripts.

- [ ] **Step 7: Run + commit**
```bash
cd /home/l0rdg3x/coding/OPNGMS/frontend && npm run test
git add frontend backend/scripts/export_openapi.py
git commit -m "feat(frontend): scaffold Vite+React+TS+Mantine, client API tipizzato, harness Vitest/MSW"
```
Expected: smoke test passes. (Commit the generated `openapi.json` + `schema.d.ts`; never `node_modules`.)

---

## Task 3: Auth — AuthProvider (via /api/me) + login + logout + ProtectedRoute

**Files:** `frontend/src/auth/{AuthProvider.tsx,useAuth.ts,ProtectedRoute.tsx}`, `frontend/src/pages/LoginPage.tsx`, `frontend/src/main.tsx`, `frontend/src/App.tsx` + tests.

- [ ] **Step 1: Failing test** — `frontend/src/auth/__tests__/login.test.tsx`:
```typescript
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import App from "../../App";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

describe("login flow", () => {
  it("shows login when unauthenticated, then the app after login", async () => {
    let authed = false;
    server.use(
      http.get("/api/me", () =>
        authed
          ? HttpResponse.json({ id: "1", email: "a@x.io", name: "A", is_superadmin: true })
          : new HttpResponse(null, { status: 401 }),
      ),
      http.post("/api/login", async () => {
        authed = true;
        return HttpResponse.json({ id: "1", email: "a@x.io", name: "A", is_superadmin: true });
      }),
      http.get("/api/me/tenants", () => HttpResponse.json([])),
    );
    renderWithProviders(<App />);
    expect(await screen.findByLabelText(/email/i)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/email/i), "a@x.io");
    await userEvent.type(screen.getByLabelText(/password/i), "pw12345");
    await userEvent.click(screen.getByRole("button", { name: /accedi/i }));
    await waitFor(() => expect(screen.getByText(/a@x.io/i)).toBeInTheDocument());
  });
});
```
NOTE: this test mounts the real `App` (router + providers). Since `renderWithProviders` already wraps in MemoryRouter, `main.tsx` (not App) owns the BrowserRouter — `App` must NOT include its own Router. Structure: `main.tsx` wraps `<App/>` in providers+BrowserRouter for production; tests use `renderWithProviders(<App/>)` which supplies MemoryRouter.

- [ ] **Step 2: Implement auth context** — `frontend/src/auth/AuthProvider.tsx`:
```typescript
import { createContext, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

export interface Me {
  id: string;
  email: string;
  name: string;
  is_superadmin: boolean;
}

interface AuthState {
  me: Me | null;
  loading: boolean;
  refresh: () => void;
}

export const AuthContext = createContext<AuthState>({
  me: null,
  loading: true,
  refresh: () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["me"],
    queryFn: async (): Promise<Me | null> => {
      const { data, response } = await api.GET("/api/me");
      if (response.status === 401) return null;
      return (data as Me) ?? null;
    },
  });
  return (
    <AuthContext.Provider
      value={{
        me: data ?? null,
        loading: isLoading,
        refresh: () => qc.invalidateQueries({ queryKey: ["me"] }),
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}
```
`frontend/src/auth/useAuth.ts`:
```typescript
import { useContext } from "react";
import { AuthContext } from "./AuthProvider";

export const useAuth = () => useContext(AuthContext);
```
`frontend/src/auth/ProtectedRoute.tsx`:
```typescript
import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "./useAuth";

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { me, loading } = useAuth();
  if (loading) return null;
  if (!me) return <Navigate to="/login" replace />;
  return <>{children}</>;
}
```

- [ ] **Step 3: Login page** — `frontend/src/pages/LoginPage.tsx`:
```typescript
import { Button, Container, Paper, PasswordInput, TextInput, Title } from "@mantine/core";
import { useForm } from "@mantine/form";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../auth/useAuth";

export function LoginPage() {
  const { refresh } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const form = useForm({ initialValues: { email: "", password: "" } });

  async function submit(values: { email: string; password: string }) {
    setError(null);
    const { response } = await api.POST("/api/login", { body: values });
    if (response.ok) {
      refresh();
      navigate("/");
    } else {
      setError("Credenziali non valide");
    }
  }

  return (
    <Container size={420} mt={80}>
      <Title order={2} ta="center" mb="lg">OPNGMS</Title>
      <Paper withBorder shadow="sm" p="lg" radius="md">
        <form onSubmit={form.onSubmit(submit)}>
          <TextInput label="Email" required {...form.getInputProps("email")} />
          <PasswordInput label="Password" required mt="md" {...form.getInputProps("password")} />
          {error && <div role="alert" style={{ color: "red", marginTop: 8 }}>{error}</div>}
          <Button type="submit" fullWidth mt="lg">Accedi</Button>
        </form>
      </Paper>
    </Container>
  );
}
```

- [ ] **Step 4: App routing** — `frontend/src/App.tsx`:
```typescript
import { Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthProvider";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import { LoginPage } from "./pages/LoginPage";
import { AppShell } from "./components/AppShell";

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/*"
          element={
            <ProtectedRoute>
              <AppShell />
            </ProtectedRoute>
          }
        />
      </Routes>
    </AuthProvider>
  );
}
```
`frontend/src/main.tsx` (providers + BrowserRouter for production):
```typescript
import { MantineProvider } from "@mantine/core";
import "@mantine/core/styles.css";
import "@mantine/notifications/styles.css";
import { Notifications } from "@mantine/notifications";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MantineProvider>
      <Notifications />
      <QueryClientProvider client={new QueryClient()}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </MantineProvider>
  </StrictMode>,
);
```
NOTE: a minimal `AppShell` placeholder is needed for this task to compile (full version in Task 4). Create a stub `components/AppShell.tsx` that shows the current user's email (so the login test's `findByText(/a@x.io/i)` passes):
```typescript
import { useAuth } from "../auth/useAuth";
export function AppShell() {
  const { me } = useAuth();
  return <div>{me?.email}</div>;
}
```

- [ ] **Step 5: Run + commit**
```bash
cd /home/l0rdg3x/coding/OPNGMS/frontend && npm run test
git add frontend/src
git commit -m "feat(frontend): auth context (/api/me) + login + ProtectedRoute"
```
Expected: login flow test + smoke test pass.

---

## Task 4: App shell + tenant switcher + logout

**Files:** `frontend/src/components/{AppShell.tsx,TenantSwitcher.tsx}`, `frontend/src/tenant/{TenantProvider.tsx,useTenant.ts}` + test.

- [ ] **Step 1: Failing test** — `frontend/src/components/__tests__/appshell.test.tsx`:
```typescript
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { AppShell } from "../AppShell";
import { AuthContext } from "../../auth/AuthProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

const me = { id: "1", email: "op@x.io", name: "Op", is_superadmin: false };

function withAuth(node: React.ReactNode) {
  return (
    <AuthContext.Provider value={{ me, loading: false, refresh: vi.fn() }}>
      {node}
    </AuthContext.Provider>
  );
}

describe("AppShell", () => {
  it("shows the tenant switcher populated from /api/me/tenants and the user email", async () => {
    server.use(
      http.get("/api/me/tenants", () =>
        HttpResponse.json([{ id: "t1", name: "Alpha", slug: "alpha", role: "operator" }]),
      ),
      http.get("/api/tenants/:tid/devices", () => HttpResponse.json([])),
    );
    renderWithProviders(withAuth(<AppShell />));
    expect(await screen.findByText("op@x.io")).toBeInTheDocument();
    expect(await screen.findByText(/Alpha/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Tenant context** — `frontend/src/tenant/TenantProvider.tsx`:
```typescript
import { createContext, type ReactNode, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

export interface MyTenant {
  id: string;
  name: string;
  slug: string;
  role: string | null;
}

interface TenantState {
  tenants: MyTenant[];
  activeId: string | null;
  setActiveId: (id: string) => void;
  loading: boolean;
}

export const TenantContext = createContext<TenantState>({
  tenants: [],
  activeId: null,
  setActiveId: () => {},
  loading: true,
});

export function TenantProvider({ children }: { children: ReactNode }) {
  const [activeId, setActiveId] = useState<string | null>(null);
  const { data, isLoading } = useQuery({
    queryKey: ["my-tenants"],
    queryFn: async (): Promise<MyTenant[]> => {
      const { data } = await api.GET("/api/me/tenants");
      return (data as MyTenant[]) ?? [];
    },
  });
  const tenants = data ?? [];
  const effectiveActive = activeId ?? tenants[0]?.id ?? null;
  return (
    <TenantContext.Provider
      value={{ tenants, activeId: effectiveActive, setActiveId, loading: isLoading }}
    >
      {children}
    </TenantContext.Provider>
  );
}
```
`frontend/src/tenant/useTenant.ts`:
```typescript
import { useContext } from "react";
import { TenantContext } from "./TenantProvider";

export const useTenant = () => useContext(TenantContext);
```

- [ ] **Step 3: TenantSwitcher** — `frontend/src/components/TenantSwitcher.tsx`:
```typescript
import { Select } from "@mantine/core";
import { useTenant } from "../tenant/useTenant";

export function TenantSwitcher() {
  const { tenants, activeId, setActiveId } = useTenant();
  if (tenants.length === 0) return <span>Nessun cliente</span>;
  return (
    <Select
      aria-label="Cliente attivo"
      data={tenants.map((t) => ({ value: t.id, label: t.name }))}
      value={activeId}
      onChange={(v) => v && setActiveId(v)}
      allowDeselect={false}
      w={220}
    />
  );
}
```

- [ ] **Step 4: AppShell** — `frontend/src/components/AppShell.tsx`:
```typescript
import { AppShell as MantineAppShell, Button, Group, NavLink, Text } from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { NavLink as RouterNavLink, Route, Routes, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { TenantProvider } from "../tenant/TenantProvider";
import { DevicesPage } from "../pages/DevicesPage";
import { DeviceDetailPage } from "../pages/DeviceDetailPage";
import { TenantSwitcher } from "./TenantSwitcher";

export function AppShell() {
  const { me, refresh } = useAuth();
  const qc = useQueryClient();
  const navigate = useNavigate();

  async function logout() {
    await api.POST("/api/logout");
    qc.clear();
    refresh();
    navigate("/login");
  }

  return (
    <TenantProvider>
      <MantineAppShell header={{ height: 56 }} navbar={{ width: 220, breakpoint: "sm" }} padding="md">
        <MantineAppShell.Header>
          <Group h="100%" px="md" justify="space-between">
            <Group>
              <Text fw={700}>OPNGMS</Text>
              <TenantSwitcher />
            </Group>
            <Group>
              <Text size="sm">{me?.email}</Text>
              <Button size="xs" variant="light" onClick={logout}>Esci</Button>
            </Group>
          </Group>
        </MantineAppShell.Header>
        <MantineAppShell.Navbar p="sm">
          <NavLink component={RouterNavLink} to="/" label="Device" />
        </MantineAppShell.Navbar>
        <MantineAppShell.Main>
          <Routes>
            <Route path="/" element={<DevicesPage />} />
            <Route path="/devices/:deviceId" element={<DeviceDetailPage />} />
          </Routes>
        </MantineAppShell.Main>
      </MantineAppShell>
    </TenantProvider>
  );
}
```
NOTE: `DevicesPage`/`DeviceDetailPage` are implemented in Tasks 5/7 — create minimal stubs now (`export function DevicesPage(){return null}` etc.) so this compiles, then flesh them out.

- [ ] **Step 5: Run + commit**
```bash
cd /home/l0rdg3x/coding/OPNGMS/frontend && npm run test
git add frontend/src
git commit -m "feat(frontend): app shell + tenant switcher + logout"
```
Expected: appshell test + previous pass.

---

## Task 5: Devices list page

**Files:** `frontend/src/pages/DevicesPage.tsx` + test.

- [ ] **Step 1: Failing test** — `frontend/src/pages/__tests__/devices.test.tsx`:
```typescript
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { DevicesPage } from "../DevicesPage";
import { TenantContext } from "../../tenant/TenantProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

function withTenant(node: React.ReactNode) {
  return (
    <TenantContext.Provider
      value={{ tenants: [{ id: "t1", name: "Alpha", slug: "alpha", role: "tenant_admin" }], activeId: "t1", setActiveId: () => {}, loading: false }}
    >
      {node}
    </TenantContext.Provider>
  );
}

describe("DevicesPage", () => {
  it("lists devices for the active tenant", async () => {
    server.use(
      http.get("/api/tenants/t1/devices", () =>
        HttpResponse.json([
          { id: "d1", tenant_id: "t1", name: "fw-edge", base_url: "https://fw", verify_tls: true, tls_fingerprint: null, site: null, tags: [], status: "reachable", last_seen: null, firmware_version: "24.7", created_at: "2026-06-09T00:00:00Z", updated_at: "2026-06-09T00:00:00Z" },
        ]),
      ),
    );
    renderWithProviders(withTenant(<DevicesPage />));
    expect(await screen.findByText("fw-edge")).toBeInTheDocument();
    expect(screen.getByText("reachable")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Implement** — `frontend/src/pages/DevicesPage.tsx`:
```typescript
import { Badge, Button, Group, Table, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { DeviceCreateModal } from "../components/DeviceCreateModal";
import { useTenant } from "../tenant/useTenant";

const STATUS_COLOR: Record<string, string> = {
  reachable: "green",
  unverified: "yellow",
  unreachable: "red",
};

export function DevicesPage() {
  const { activeId } = useTenant();
  const navigate = useNavigate();
  const [createOpen, setCreateOpen] = useState(false);
  const { data: devices } = useQuery({
    queryKey: ["devices", activeId],
    enabled: !!activeId,
    queryFn: async () => {
      const { data } = await api.GET("/api/tenants/{tenant_id}/devices", {
        params: { path: { tenant_id: activeId! } },
      });
      return data ?? [];
    },
  });

  return (
    <>
      <Group justify="space-between" mb="md">
        <Title order={3}>Device</Title>
        <Button onClick={() => setCreateOpen(true)} disabled={!activeId}>Aggiungi device</Button>
      </Group>
      <Table highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Nome</Table.Th><Table.Th>URL</Table.Th>
            <Table.Th>Stato</Table.Th><Table.Th>Firmware</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {(devices ?? []).map((d) => (
            <Table.Tr key={d.id} style={{ cursor: "pointer" }} onClick={() => navigate(`/devices/${d.id}`)}>
              <Table.Td>{d.name}</Table.Td>
              <Table.Td>{d.base_url}</Table.Td>
              <Table.Td><Badge color={STATUS_COLOR[d.status] ?? "gray"}>{d.status}</Badge></Table.Td>
              <Table.Td>{d.firmware_version ?? "—"}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {activeId && (
        <DeviceCreateModal tenantId={activeId} opened={createOpen} onClose={() => setCreateOpen(false)} />
      )}
    </>
  );
}
```
NOTE: `DeviceCreateModal` is Task 6 — create a stub now (`export function DeviceCreateModal(){return null}`).

- [ ] **Step 3: Run + commit**
```bash
cd /home/l0rdg3x/coding/OPNGMS/frontend && npm run test
git add frontend/src
git commit -m "feat(frontend): pagina lista device (per cliente attivo)"
```

---

## Task 6: Device create modal (onboarding)

**Files:** `frontend/src/components/DeviceCreateModal.tsx` + test.

- [ ] **Step 1: Failing test** — `frontend/src/components/__tests__/devicecreate.test.tsx`:
```typescript
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { DeviceCreateModal } from "../DeviceCreateModal";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

describe("DeviceCreateModal", () => {
  it("submits onboarding and closes on success", async () => {
    const onClose = vi.fn();
    let posted: any = null;
    server.use(
      http.post("/api/tenants/t1/devices", async ({ request }) => {
        posted = await request.json();
        return HttpResponse.json({ id: "d1", name: posted.name, status: "reachable" }, { status: 201 });
      }),
      http.get("/api/tenants/t1/devices", () => HttpResponse.json([])),
    );
    renderWithProviders(<DeviceCreateModal tenantId="t1" opened onClose={onClose} />);
    await userEvent.type(screen.getByLabelText(/nome/i), "fw1");
    await userEvent.type(screen.getByLabelText(/url/i), "https://fw1");
    await userEvent.type(screen.getByLabelText(/api key/i), "k");
    await userEvent.type(screen.getByLabelText(/api secret/i), "s");
    await userEvent.click(screen.getByRole("button", { name: /salva/i }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(posted.base_url).toBe("https://fw1");
  });
});
```

- [ ] **Step 2: Implement** — `frontend/src/components/DeviceCreateModal.tsx`:
```typescript
import { Button, Modal, PasswordInput, Switch, TextInput } from "@mantine/core";
import { useForm } from "@mantine/form";
import { notifications } from "@mantine/notifications";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

interface Props {
  tenantId: string;
  opened: boolean;
  onClose: () => void;
}

export function DeviceCreateModal({ tenantId, opened, onClose }: Props) {
  const qc = useQueryClient();
  const form = useForm({
    initialValues: { name: "", base_url: "", api_key: "", api_secret: "", verify_tls: true },
  });

  const mutation = useMutation({
    mutationFn: async (values: typeof form.values) => {
      const { data, error } = await api.POST("/api/tenants/{tenant_id}/devices", {
        params: { path: { tenant_id: tenantId } },
        body: values,
      });
      if (error) throw new Error("create failed");
      return data;
    },
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["devices", tenantId] });
      notifications.show({ message: `Device creato (${(d as { status: string }).status})` });
      form.reset();
      onClose();
    },
    onError: () => notifications.show({ color: "red", message: "Creazione fallita" }),
  });

  return (
    <Modal opened={opened} onClose={onClose} title="Aggiungi device">
      <form onSubmit={form.onSubmit((v) => mutation.mutate(v))}>
        <TextInput label="Nome" required {...form.getInputProps("name")} />
        <TextInput label="URL (https)" required mt="sm" {...form.getInputProps("base_url")} />
        <TextInput label="API key" required mt="sm" {...form.getInputProps("api_key")} />
        <PasswordInput label="API secret" required mt="sm" {...form.getInputProps("api_secret")} />
        <Switch label="Verifica TLS" mt="md" {...form.getInputProps("verify_tls", { type: "checkbox" })} />
        <Button type="submit" mt="lg" loading={mutation.isPending}>Salva</Button>
      </form>
    </Modal>
  );
}
```

- [ ] **Step 3: Run + commit**
```bash
cd /home/l0rdg3x/coding/OPNGMS/frontend && npm run test
git add frontend/src
git commit -m "feat(frontend): modal creazione device (onboarding)"
```

---

## Task 7: Device detail + azioni (test-connection, rotate-secret, edit, delete)

**Files:** `frontend/src/pages/DeviceDetailPage.tsx`, `frontend/src/components/DeviceActions.tsx` + test.

- [ ] **Step 1: Failing test** — `frontend/src/pages/__tests__/devicedetail.test.tsx`:
```typescript
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { DeviceDetailPage } from "../DeviceDetailPage";
import { TenantContext } from "../../tenant/TenantProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

function withTenant(node: React.ReactNode) {
  return (
    <TenantContext.Provider value={{ tenants: [{ id: "t1", name: "A", slug: "a", role: "tenant_admin" }], activeId: "t1", setActiveId: () => {}, loading: false }}>
      {node}
    </TenantContext.Provider>
  );
}

const device = { id: "d1", tenant_id: "t1", name: "fw1", base_url: "https://fw1", verify_tls: true, tls_fingerprint: null, site: null, tags: [], status: "unverified", last_seen: null, firmware_version: null, created_at: "2026-06-09T00:00:00Z", updated_at: "2026-06-09T00:00:00Z" };

describe("DeviceDetailPage", () => {
  it("shows device and runs test-connection", async () => {
    server.use(
      http.get("/api/tenants/t1/devices/d1", () => HttpResponse.json(device)),
      http.post("/api/tenants/t1/devices/d1/test-connection", () =>
        HttpResponse.json({ status: "reachable", firmware_version: "24.7", error: null }),
      ),
    );
    renderWithProviders(withTenant(<DeviceDetailPage />), { route: "/devices/d1" });
    expect(await screen.findByRole("heading", { name: "fw1" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /testa connessione/i }));
    await waitFor(() => expect(screen.getByText(/reachable/i)).toBeInTheDocument());
  });
});
```
NOTE: the test renders the page directly at route `/devices/d1`; the page reads `:deviceId` via `useParams`. `renderWithProviders` uses MemoryRouter with `initialEntries=[route]`, but `useParams` needs a matching `<Route>` — wrap the page in a `<Routes><Route path="/devices/:deviceId" .../></Routes>` inside the test, OR have the test pass the id via a Routes wrapper. Implement the page to read `useParams`; in the test, wrap: `renderWithProviders(withTenant(<Routes><Route path="/devices/:deviceId" element={<DeviceDetailPage/>} /></Routes>), { route: "/devices/d1" })` (import Routes/Route from react-router-dom in the test).

- [ ] **Step 2: Implement DeviceActions** — `frontend/src/components/DeviceActions.tsx`:
```typescript
import { Button, Group } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";

export function DeviceActions({ tenantId, deviceId }: { tenantId: string; deviceId: string }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const path = { params: { path: { tenant_id: tenantId, device_id: deviceId } } } as const;

  const test = useMutation({
    mutationFn: async () => {
      const { data } = await api.POST("/api/tenants/{tenant_id}/devices/{device_id}/test-connection", path);
      return data;
    },
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["device", tenantId, deviceId] });
      notifications.show({ message: `Test: ${(d as { status: string }).status}` });
    },
  });

  const remove = useMutation({
    mutationFn: async () => {
      await api.DELETE("/api/tenants/{tenant_id}/devices/{device_id}", path);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["devices", tenantId] });
      navigate("/");
    },
  });

  return (
    <Group mt="md">
      <Button onClick={() => test.mutate()} loading={test.isPending}>Testa connessione</Button>
      <Button color="red" variant="light" onClick={() => remove.mutate()}>Elimina</Button>
    </Group>
  );
}
```

- [ ] **Step 3: Implement DeviceDetailPage** — `frontend/src/pages/DeviceDetailPage.tsx`:
```typescript
import { Badge, Card, Stack, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import { DeviceActions } from "../components/DeviceActions";
import { useTenant } from "../tenant/useTenant";

export function DeviceDetailPage() {
  const { deviceId } = useParams();
  const { activeId } = useTenant();
  const { data: device } = useQuery({
    queryKey: ["device", activeId, deviceId],
    enabled: !!activeId && !!deviceId,
    queryFn: async () => {
      const { data } = await api.GET("/api/tenants/{tenant_id}/devices/{device_id}", {
        params: { path: { tenant_id: activeId!, device_id: deviceId! } },
      });
      return data;
    },
  });
  if (!device) return null;
  return (
    <Stack>
      <Title order={3}>{device.name}</Title>
      <Card withBorder>
        <Text>URL: {device.base_url}</Text>
        <Text>Stato: <Badge>{device.status}</Badge></Text>
        <Text>Firmware: {device.firmware_version ?? "—"}</Text>
      </Card>
      {activeId && deviceId && <DeviceActions tenantId={activeId} deviceId={deviceId} />}
    </Stack>
  );
}
```

- [ ] **Step 4: Run + commit**
```bash
cd /home/l0rdg3x/coding/OPNGMS/frontend && npm run test
git add frontend/src
git commit -m "feat(frontend): dettaglio device + azioni (test-connection, elimina)"
```

---

## Task 8: Build prod + README + verifica suite

**Files:** `frontend/README.md`.

- [ ] **Step 1: Type-check + production build** (catches TS errors the test runner might miss):
```bash
cd /home/l0rdg3x/coding/OPNGMS/frontend && npm run build
```
Expected: `tsc` + `vite build` succeed (no type errors). Fix any type errors surfaced.

- [ ] **Step 2: Full frontend test suite**
```bash
cd /home/l0rdg3x/coding/OPNGMS/frontend && npm run test
```
Expected: all tests green.

- [ ] **Step 3: README** — `frontend/README.md`:
```markdown
# OPNGMS Frontend

Console web React per OPNGMS (Fase 1 · Milestone D): login, app shell con tenant switcher,
gestione device.

## Stack
Vite + React + TypeScript, Mantine, React Router, TanStack Query, client API tipizzato
(openapi-fetch) generato da OpenAPI, test Vitest + Testing Library + MSW.

## Sviluppo
1. `npm install`
2. Genera il client API dal backend (richiede il venv del backend): `npm run gen:api`
3. Avvia il backend (`cd ../backend && .venv/bin/uvicorn app.main:app`) su :8000
4. `npm run dev` → http://localhost:5173 (proxy `/api` → :8000)

## Test
`npm run test`  ·  Build: `npm run build`

## Rigenerare il client API
Dopo modifiche all'API backend: `npm run gen:api` (riesporta `openapi.json` + `src/api/schema.d.ts`).
```

- [ ] **Step 4: Commit**
```bash
git add frontend/README.md frontend/package.json
git commit -m "docs(frontend): README + verifica build/test Milestone D"
```

---

## Self-review (mappatura spec → task)
- **Spec §15 shell frontend** (Vite+React+TS, client API tipizzato da OpenAPI, auth context, app
  shell con tenant switcher, lista/dettaglio device): Task 2 (scaffold+client), Task 3 (auth),
  Task 4 (shell+switcher), Task 5-7 (device UI).
- **Auth a cookie httpOnly**: il frontend deriva lo stato da `GET /api/me` (Task 3); `credentials:
  'include'` + middleware CSRF su mutazioni (Task 2).
- **Tenant switcher**: alimentato da `GET /api/me/tenants` (Task 1 backend + Task 4 frontend).
- **CRUD device + onboarding + test/rotate**: Task 5 (lista), 6 (create/onboarding), 7
  (dettaglio + test-connection + delete). *YAGNI:* edit e rotate-secret UI sono cablabili come i
  pattern in Task 6/7 (mutation + form) — inclusi solo create/test/delete nell'MVP; edit/rotate
  UI sono un follow-up immediato se servono.

**Note di scope / debito:**
- **Org-admin UI** (tenant/utenti/membership) = follow-up (scope focalizzato scelto).
- **rotate-secret / edit device UI**: i pattern esistono (Task 6/7); aggiungerli è meccanico.
- Il client API generato (`schema.d.ts`) va **rigenerato** (`npm run gen:api`) a ogni cambio
  dell'API backend, altrimenti i tipi divergono.
- Nessun test e2e browser (Playwright) in questa milestone — i test MSW coprono i flussi a livello
  componente/integrazione. Un e2e contro il backend reale è un possibile follow-up.

**Placeholder scan:** ogni step ha codice/comando concreto. Gli stub temporanei
(`AppShell`/`DevicesPage`/`DeviceCreateModal`/`DeviceDetailPage`) sono creati esplicitamente nel
task che li introduce e completati nel task dedicato — nessun TODO lasciato aperto.
**Type consistency:** `api.GET/POST/PATCH/DELETE` con `params.path.{tenant_id,device_id}`,
`useAuth()/{me,loading,refresh}`, `useTenant()/{tenants,activeId,setActiveId}`, MSW handlers
allineati ai path reali, coerenti tra i Task 2-7.

---

## Debito tecnico (dalla review olistica finale — READY WITH MINOR NOTES)

Zero issue Critical/Important. Sicurezza frontend solida (auth a fonte unica, cookie httpOnly mai
letto lato client, CSRF esatto, segreti write-only, isolamento tenant via query key). Da tracciare:

1. **Niente loading/error UI** su lista/dettaglio device (il dettaglio rende `null` finché i dati
   non arrivano) — aggiungere skeleton/stati d'errore.
2. **Niente dialog di conferma sull'elimina** — l'azione distruttiva parte con un click solo.
3. **UI org-admin** (tenant/utenti/membership) rinviata (scope focalizzato).
4. **UI edit-device e rotate-secret** rinviate (gli endpoint backend `PATCH` e `/rotate-secret`
   esistono ma il frontend non li usa ancora).
5. **Nessun e2e Playwright** — solo test componente/integrazione (Vitest+RTL+MSW).
6. **`schema.d.ts`/`openapi.json` generati** — rigenerare con `npm run gen:api` a ogni cambio API.
7. **3 errori ESLint** (context+component nello stesso file → fast-refresh; reference triple-slash
   di vitest) — fixare (separare i context) o cablare lint nella CI; oggi fuori dal gate build/test.
8. **Bundle unico ~535 kB** (Vite avvisa >500 kB) — valutare code-splitting.
9. **`activeId` non persistito** tra i reload (riparte da `tenants[0]`).
