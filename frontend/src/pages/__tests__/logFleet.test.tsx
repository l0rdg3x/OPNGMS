import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { LogFleetPage } from "../LogFleetPage";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

const FLEET = "http://localhost:3000/api/admin/log-fleet";
const DEVICES = "http://localhost:3000/api/admin/log-fleet/tenants/a/devices";
const SILENT = "http://localhost:3000/api/admin/silent-tenant-alerts";

function devicesHandler() {
  return http.get(DEVICES, ({ request }) => {
    const window = new URL(request.url).searchParams.get("window") ?? "24h";
    return HttpResponse.json({
      tenant_id: "a",
      tenant_name: "Acme",
      devices: [
        { device_id: "d1", name: "fw-live", forwarding: "enabled",
          last_log_at: new Date(Date.now() - 60 * 1000).toISOString(), volume: 7, is_silent: false },
        { device_id: "d2", name: "fw-quiet", forwarding: "enabled",
          last_log_at: null, volume: null, is_silent: true },
      ],
      totals: { enabled_devices: 2, silent_devices: 1, volume: 7 },
      window,
    });
  });
}

function fleetHandler(seenWindows?: string[]) {
  return http.get(FLEET, ({ request }) => {
    const window = new URL(request.url).searchParams.get("window") ?? "24h";
    seenWindows?.push(window);
    // Beta logged "just now" (within the 1h staleness window) so it is NOT silent;
    // a fresh timestamp keeps the test deterministic against the wall clock.
    const recent = new Date(Date.now() - 60 * 1000).toISOString();
    return HttpResponse.json({
      tenants: [
        { tenant_id: "a", tenant_name: "Acme", enabled: 2, disabled: 0, revoked: 0,
          total_devices: 2, last_log_at: null, volume: null },
        { tenant_id: "b", tenant_name: "Beta", enabled: 1, disabled: 1, revoked: 0,
          total_devices: 2, last_log_at: recent, volume: 42 },
      ],
      totals: { tenants_with_forwarding: 2, enabled_devices: 3, volume: 42, silent_tenants: 1 },
      window,
    });
  });
}

describe("LogFleetPage", () => {
  // Default: no silent-tenant alerts (overridable per-test). Avoids unhandled-request errors
  // from the banner hook that fires on every render.
  beforeEach(() => server.use(http.get(SILENT, () => HttpResponse.json([]))));

  it("renders totals + per-tenant rows and flags a silent tenant", async () => {
    server.use(fleetHandler());
    renderWithProviders(<LogFleetPage />);
    expect(await screen.findByText("Acme")).toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();
    expect(screen.getByTestId("fleet-silent-count")).toHaveTextContent("1");
    expect(screen.getByTestId("fleet-silent-a")).toBeInTheDocument();
    expect(screen.queryByTestId("fleet-silent-b")).toBeNull();
  });

  it("re-fetches with the selected window", async () => {
    const seen: string[] = [];
    server.use(fleetHandler(seen));
    renderWithProviders(<LogFleetPage />);
    await screen.findByText("Acme");
    expect(seen).toContain("24h");

    await userEvent.click(screen.getByText("7d"));
    await waitFor(() => expect(seen).toContain("7d"));
  });

  it("shows a banner when there are active silent-tenant alerts", async () => {
    server.use(
      fleetHandler(),
      http.get(SILENT, () =>
        HttpResponse.json([
          { tenant_id: "a", tenant_name: "Acme", silent_since: "2026-06-12T06:00:00Z" },
        ]),
      ),
    );
    renderWithProviders(<LogFleetPage />);
    const banner = await screen.findByTestId("silent-alert-banner");
    expect(banner).toHaveTextContent("Acme");
  });

  it("exports CSV for the currently selected window", async () => {
    const cap: { url?: string } = {};
    const origCreate = URL.createObjectURL;
    const origRevoke = URL.revokeObjectURL;
    URL.createObjectURL = vi.fn(() => "blob:x");
    URL.revokeObjectURL = vi.fn();
    try {
      server.use(
        fleetHandler(),
        http.get("http://localhost:3000/api/admin/log-fleet/export", ({ request }) => {
          cap.url = request.url;
          return HttpResponse.text("tenant_name,enabled\nAcme,2\n", {
            headers: { "Content-Type": "text/csv" },
          });
        }),
      );
      renderWithProviders(<LogFleetPage />);
      await screen.findByText("Acme");
      await userEvent.click(screen.getByRole("button", { name: /export csv/i }));
      await waitFor(() => expect(cap.url).toBeTruthy());
      const u = new URL(cap.url!);
      expect(u.searchParams.get("format")).toBe("csv");
      expect(u.searchParams.get("window")).toBe("24h");
    } finally {
      URL.createObjectURL = origCreate;
      URL.revokeObjectURL = origRevoke;
    }
  });

  it("drills into a tenant's per-device list with a silent device flagged", async () => {
    server.use(fleetHandler(), devicesHandler());
    renderWithProviders(<LogFleetPage />);
    await screen.findByText("Acme");

    await userEvent.click(screen.getByTestId("fleet-row-a"));

    expect(await screen.findByText("fw-quiet")).toBeInTheDocument();
    expect(screen.getByText("fw-live")).toBeInTheDocument();
    // the server-flagged silent device shows a badge; the live one does not
    expect(screen.getByTestId("device-silent-d2")).toBeInTheDocument();
    expect(screen.queryByTestId("device-silent-d1")).toBeNull();
  });
});
