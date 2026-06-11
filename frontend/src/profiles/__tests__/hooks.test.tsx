import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { server } from "../../test/server";
import { I18nProvider } from "../../i18n";
import { TenantContext } from "../../tenant/TenantProvider";
import { useCreateProfile, useProfiles, useApplyProfile } from "../hooks";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <I18nProvider>
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    </I18nProvider>
  );
}

function wrapperWithTenant({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <I18nProvider>
      <TenantContext.Provider
        value={{
          tenants: [{ id: "t1", name: "A", slug: "a", role: "tenant_admin" }],
          activeId: "t1",
          setActiveId: () => {},
          loading: false,
        }}
      >
        <QueryClientProvider client={qc}>{children}</QueryClientProvider>
      </TenantContext.Provider>
    </I18nProvider>
  );
}

const P = {
  id: "p1",
  name: "baseline",
  description: "A baseline profile",
  version: 1,
  template_ids: ["t1", "t2"],
  created_at: "2026-06-11T00:00:00Z",
  updated_at: "2026-06-11T00:00:00Z",
};

describe("profile library hooks", () => {
  it("useProfiles lists /api/profiles", async () => {
    server.use(http.get("/api/profiles", () => HttpResponse.json([P])));
    const { result } = renderHook(() => useProfiles(), { wrapper });
    await waitFor(() => expect(result.current.data?.length).toBe(1));
    expect(result.current.data?.[0].name).toBe("baseline");
  });

  it("useCreateProfile POSTs {name, description, template_ids}", async () => {
    let captured: unknown = null;
    server.use(
      http.post("/api/profiles", async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json(P, { status: 201 });
      }),
    );
    const { result } = renderHook(() => useCreateProfile(), { wrapper });
    await result.current.mutateAsync({
      name: "baseline",
      description: "A baseline profile",
      template_ids: ["t1", "t2"],
    });
    expect(captured).toMatchObject({
      name: "baseline",
      description: "A baseline profile",
      template_ids: ["t1", "t2"],
    });
  });

  it("useApplyProfile POSTs to .../profiles/{id}/apply with {scheduled_at, bindings}", async () => {
    let captured: unknown = null;
    server.use(
      http.post(
        "/api/tenants/t1/devices/d1/profiles/p1/apply",
        async ({ request }) => {
          captured = await request.json();
          return HttpResponse.json({ change_ids: ["c1"], status: "pending" });
        },
      ),
    );
    const { result } = renderHook(() => useApplyProfile("d1"), {
      wrapper: wrapperWithTenant,
    });
    await result.current.mutateAsync({
      profileId: "p1",
      scheduled_at: null,
      bindings: { interface: "wan" },
    });
    expect(captured).toMatchObject({ scheduled_at: null, bindings: { interface: "wan" } });
  });
});
