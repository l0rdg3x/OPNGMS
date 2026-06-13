import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it } from "vitest";
import { OverviewPage } from "../OverviewPage";
import { TenantContext } from "../../tenant/TenantProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

function withTenant(node: ReactNode) {
  return (
    <TenantContext.Provider
      value={{
        tenants: [{ id: "t1", name: "A", slug: "a", role: "tenant_admin" }],
        activeId: "t1",
        setActiveId: () => {},
        loading: false,
      }}
    >
      {node}
    </TenantContext.Provider>
  );
}

describe("OverviewPage", () => {
  // The page renders the attacker-countries widget, which fires its own request;
  // default it to an empty list so these tests focus on health/alerts only.
  beforeEach(() => {
    server.use(
      http.get("/api/tenants/t1/attacker-countries", () => HttpResponse.json([])),
    );
  });

  it("shows health and active alerts", async () => {
    server.use(
      http.get("/api/tenants/t1/health", () =>
        HttpResponse.json({ total_devices: 5, by_status: { reachable: 2 }, active_alerts: 1 }),
      ),
      http.get("/api/tenants/t1/alerts", () =>
        HttpResponse.json([
          {
            id: "a1", device_id: "d1", type: "device.down", label: "", severity: "critical",
            opened_at: "2026-06-09T10:00:00Z", resolved_at: null, details: {},
          },
        ]),
      ),
    );
    renderWithProviders(withTenant(<OverviewPage />));
    expect(await screen.findByText("5")).toBeInTheDocument();
    expect(await screen.findByText(/device\.down/)).toBeInTheDocument();
  });

  it("empty-state with no active alerts", async () => {
    server.use(
      http.get("/api/tenants/t1/health", () =>
        HttpResponse.json({ total_devices: 0, by_status: {}, active_alerts: 0 }),
      ),
      http.get("/api/tenants/t1/alerts", () => HttpResponse.json([])),
    );
    renderWithProviders(withTenant(<OverviewPage />));
    expect(await screen.findByText(/no active alerts/i)).toBeInTheDocument();
  });

  it("shows an error message when the alerts API returns 500", async () => {
    // Locks in the error branch: without the throw in the hook, useAlerts
    // would `return data ?? []` on a 500 -> no error propagated,
    // and the red Alert would be dead code. With the fix the error propagates.
    server.use(
      http.get("/api/tenants/t1/health", () =>
        HttpResponse.json({ total_devices: 0, by_status: {}, active_alerts: 0 }),
      ),
      http.get("/api/tenants/t1/alerts", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    renderWithProviders(withTenant(<OverviewPage />));
    expect(await screen.findByText(/failed to load alerts/i)).toBeInTheDocument();
  });
});
