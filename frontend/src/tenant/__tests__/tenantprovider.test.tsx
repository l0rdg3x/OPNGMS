import { screen, act, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it, beforeEach, vi } from "vitest";
import { renderWithProviders } from "../../test/utils";
import { TenantProvider } from "../TenantProvider";
import { useTenant } from "../useTenant";
import { server } from "../../test/server";

const TENANTS = [
  { id: "t1", name: "Alpha", slug: "alpha", role: "operator" },
  { id: "t2", name: "Beta", slug: "beta", role: "tenant_admin" },
];

const LS_KEY = "opngms.activeTenantId";

// jsdom provides localStorage on globalThis; in some vitest pool configs the
// global may not be wired yet at import time, so we use a manual in-memory stub.
const localStorageStub = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (k: string) => store[k] ?? null,
    setItem: (k: string, v: string) => { store[k] = v; },
    removeItem: (k: string) => { delete store[k]; },
    clear: () => { store = {}; },
  };
})();
vi.stubGlobal("localStorage", localStorageStub);

let capturedSetActiveId: ((id: string) => void) | null = null;

function TenantDisplay() {
  const { activeId, setActiveId } = useTenant();
  capturedSetActiveId = setActiveId;
  return <div data-testid="active-id">{activeId ?? "none"}</div>;
}

function Wrapper() {
  return (
    <TenantProvider>
      <TenantDisplay />
    </TenantProvider>
  );
}

describe("TenantProvider – localStorage persistence", () => {
  beforeEach(() => {
    localStorageStub.clear();
    capturedSetActiveId = null;
  });

  it("restores the persisted tenant id on mount", async () => {
    localStorageStub.setItem(LS_KEY, "t2");
    server.use(http.get("/api/me/tenants", () => HttpResponse.json(TENANTS)));

    renderWithProviders(<Wrapper />);

    // Wait for tenant list to load and the active id to settle on the stored value
    await waitFor(() =>
      expect(screen.getByTestId("active-id")).toHaveTextContent("t2"),
    );
  });

  it("persists to localStorage when setActiveId is called", async () => {
    server.use(http.get("/api/me/tenants", () => HttpResponse.json(TENANTS)));

    renderWithProviders(<Wrapper />);

    // Wait for tenants to load; default will be t1
    await waitFor(() =>
      expect(screen.getByTestId("active-id")).toHaveTextContent("t1"),
    );

    // Directly call the setter captured from the hook
    act(() => capturedSetActiveId!("t2"));

    await waitFor(() => expect(localStorageStub.getItem(LS_KEY)).toBe("t2"));
    expect(screen.getByTestId("active-id")).toHaveTextContent("t2");
  });

  it("falls back to the first tenant when the stored id is no longer in the list", async () => {
    localStorageStub.setItem(LS_KEY, "t-gone");
    server.use(http.get("/api/me/tenants", () => HttpResponse.json(TENANTS)));

    renderWithProviders(<Wrapper />);

    // Should fall back to "t1" (the first in the list)
    await waitFor(() =>
      expect(screen.getByTestId("active-id")).toHaveTextContent("t1"),
    );
  });
});
