import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { LogFleetPage } from "../LogFleetPage";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

const FLEET = "http://localhost:3000/api/admin/log-fleet";

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
});
