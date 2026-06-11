import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { server } from "../../test/server";
import { I18nProvider } from "../../i18n";
import { TenantContext } from "../../tenant/TenantProvider";
import { useCreateFirmwareAction, useFirmwareActions } from "../hooks";

// MSW URLs are RELATIVE — the openapi-fetch client uses baseUrl "" in tests (VITE_API_BASE unset).
const BASE = "/api/tenants/t1/devices/d1/firmware";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <I18nProvider>
      <QueryClientProvider client={qc}>
        <TenantContext.Provider
          value={{
            tenants: [{ id: "t1", name: "A", slug: "a", role: "tenant_admin" }],
            activeId: "t1",
            setActiveId: () => {},
            loading: false,
          }}
        >
          {children}
        </TenantContext.Provider>
      </QueryClientProvider>
    </I18nProvider>
  );
}

describe("firmware hooks", () => {
  it("useFirmwareActions loads the actions list", async () => {
    server.use(
      http.get(`${BASE}/actions`, () =>
        HttpResponse.json([
          { id: "a1", kind: "firmware_update", target: "", status: "done",
            scheduled_at: null, applied_at: null, result: { version: "26.1.9" },
            created_at: "2026-06-11T00:00:00Z" },
        ]),
      ),
    );
    const { result } = renderHook(() => useFirmwareActions("d1"), { wrapper });
    await waitFor(() => expect(result.current.data?.length).toBe(1));
    expect(result.current.data?.[0].kind).toBe("firmware_update");
  });

  it("useCreateFirmwareAction POSTs the body and returns the created action", async () => {
    let captured: unknown = null;
    server.use(
      http.post(`${BASE}/action`, async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json(
          { id: "a2", kind: "plugin_install", target: "os-acme-client", status: "scheduled",
            scheduled_at: null, applied_at: null, result: {}, created_at: "2026-06-11T00:00:00Z" },
          { status: 201 },
        );
      }),
    );
    const { result } = renderHook(() => useCreateFirmwareAction("d1"), { wrapper });
    const created = await result.current.mutateAsync({
      kind: "plugin_install", target: "os-acme-client", scheduled_at: null,
    });
    expect(created.id).toBe("a2");
    expect(captured).toMatchObject({ kind: "plugin_install", target: "os-acme-client" });
  });
});
