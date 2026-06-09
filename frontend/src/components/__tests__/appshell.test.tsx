import { screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { AppShell } from "../AppShell";
import { AuthContext } from "../../auth/AuthProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

const me = { id: "1", email: "op@x.io", name: "Op", is_superadmin: false };

function withAuth(node: ReactNode) {
  return (
    <AuthContext.Provider value={{ me, loading: false, refresh: vi.fn(), setMe: vi.fn() }}>
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
      // La landing route "/" ora monta OverviewPage, che interroga /health e /alerts.
      http.get("/api/tenants/t1/health", () =>
        HttpResponse.json({ total_devices: 0, by_status: {}, active_alerts: 0 }),
      ),
      http.get("/api/tenants/t1/alerts", () => HttpResponse.json([])),
    );
    renderWithProviders(withAuth(<AppShell />));
    expect(await screen.findByText("op@x.io")).toBeInTheDocument();
    expect(await screen.findByText(/Alpha/)).toBeInTheDocument();
  });
});
