import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { server } from "../../test/server";
import { I18nProvider } from "../../i18n";
import { useCreateTemplate, useTemplates } from "../hooks";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <I18nProvider><QueryClientProvider client={qc}>{children}</QueryClientProvider></I18nProvider>;
}

const T = { id: "x1", kind: "firewall_alias", name: "web", description: "", version: 1,
  body: { name: "web", type: "host", content: ["1.2.3.4"], description: "" },
  created_at: "2026-06-11T00:00:00Z", updated_at: "2026-06-11T00:00:00Z" };

describe("template library hooks", () => {
  it("useTemplates lists the library", async () => {
    server.use(http.get("/api/templates", () => HttpResponse.json([T])));
    const { result } = renderHook(() => useTemplates(), { wrapper });
    await waitFor(() => expect(result.current.data?.length).toBe(1));
    expect(result.current.data?.[0].name).toBe("web");
  });

  it("useCreateTemplate POSTs the body", async () => {
    let captured: unknown = null;
    server.use(http.post("/api/templates", async ({ request }) => {
      captured = await request.json();
      return HttpResponse.json(T, { status: 201 });
    }));
    const { result } = renderHook(() => useCreateTemplate(), { wrapper });
    await result.current.mutateAsync({ kind: "firewall_alias", name: "web", description: "",
      body: { name: "web", type: "host", content: ["1.2.3.4"], description: "" } });
    expect(captured).toMatchObject({ name: "web", kind: "firewall_alias" });
  });
});
