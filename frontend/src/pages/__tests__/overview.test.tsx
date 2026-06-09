import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
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
  it("mostra health e alert attivi", async () => {
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

  it("empty-state senza alert attivi", async () => {
    server.use(
      http.get("/api/tenants/t1/health", () =>
        HttpResponse.json({ total_devices: 0, by_status: {}, active_alerts: 0 }),
      ),
      http.get("/api/tenants/t1/alerts", () => HttpResponse.json([])),
    );
    renderWithProviders(withTenant(<OverviewPage />));
    expect(await screen.findByText(/nessun alert attivo/i)).toBeInTheDocument();
  });

  it("mostra messaggio d'errore quando l'API alert ritorna 500", async () => {
    // Lock-in del ramo d'errore: senza il throw nello hook, useAlerts
    // farebbe `return data ?? []` su un 500 -> nessun errore propagato,
    // l'Alert rosso resterebbe dead code. Con il fix l'errore si propaga.
    server.use(
      http.get("/api/tenants/t1/health", () =>
        HttpResponse.json({ total_devices: 0, by_status: {}, active_alerts: 0 }),
      ),
      http.get("/api/tenants/t1/alerts", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    renderWithProviders(withTenant(<OverviewPage />));
    expect(await screen.findByText(/errore nel caricamento degli alert/i)).toBeInTheDocument();
  });
});
