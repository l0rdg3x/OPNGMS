import { screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { AppShell } from "../AppShell";
import { AuthContext } from "../../auth/AuthProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

const me = { id: "1", email: "op@x.io", name: "Op", is_superadmin: false };

function withAuth(node: ReactNode, is_superadmin = false) {
  return (
    <AuthContext.Provider
      value={{ me: { ...me, is_superadmin }, loading: false, refresh: vi.fn(), setMe: vi.fn() }}
    >
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
      // The landing route "/" now mounts OverviewPage, which queries /health and /alerts.
      http.get("/api/tenants/t1/health", () =>
        HttpResponse.json({ total_devices: 0, by_status: {}, active_alerts: 0 }),
      ),
      http.get("/api/tenants/t1/alerts", () => HttpResponse.json([])),
    );
    renderWithProviders(withAuth(<AppShell />));
    expect(await screen.findByText("op@x.io")).toBeInTheDocument();
    expect(await screen.findByText(/Alpha/)).toBeInTheDocument();
  });

  it("shows the Template library nav link only for superadmins", async () => {
    server.use(
      http.get("/api/me/tenants", () =>
        HttpResponse.json([{ id: "t1", name: "Alpha", slug: "alpha", role: "operator" }]),
      ),
      http.get("/api/tenants/t1/health", () =>
        HttpResponse.json({ total_devices: 0, by_status: {}, active_alerts: 0 }),
      ),
      http.get("/api/tenants/t1/alerts", () => HttpResponse.json([])),
    );

    // Superadmin: link should be present
    const { unmount } = renderWithProviders(withAuth(<AppShell />, true));
    expect(
      await screen.findByRole("link", { name: /template library/i }),
    ).toBeInTheDocument();
    unmount();

    // Non-superadmin: link should be absent
    renderWithProviders(withAuth(<AppShell />, false));
    // Wait for the shell to render (use the email as a proxy)
    expect(await screen.findByText("op@x.io")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /template library/i })).toBeNull();
  });

  it("redirects a non-superadmin who navigates directly to /admin/log-fleet (route guard)", async () => {
    server.use(
      http.get("/api/me/tenants", () =>
        HttpResponse.json([{ id: "t1", name: "Alpha", slug: "alpha", role: "operator" }]),
      ),
      http.get("/api/tenants/t1/health", () =>
        HttpResponse.json({ total_devices: 0, by_status: {}, active_alerts: 0 }),
      ),
      http.get("/api/tenants/t1/alerts", () => HttpResponse.json([])),
    );
    // If the guard failed, LogFleetPage would render + fire /api/admin/log-fleet (unhandled -> test
    // error). Instead the user is redirected home and the page title never appears.
    renderWithProviders(withAuth(<AppShell />, false), { route: "/admin/log-fleet" });
    expect(await screen.findByText("op@x.io")).toBeInTheDocument();
    expect(screen.queryByText("Log fleet")).toBeNull();
  });
});
