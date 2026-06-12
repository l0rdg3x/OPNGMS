import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";

import { LogFleetPage } from "../LogFleetPage";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

const FLEET = "http://localhost:3000/api/admin/log-fleet";

describe("LogFleetPage", () => {
  it("renders totals + per-tenant rows and flags a silent tenant", async () => {
    // Beta logged "just now" (within the 1h staleness window) so it is NOT silent;
    // a fresh timestamp keeps the test deterministic against the wall clock.
    const recent = new Date(Date.now() - 60 * 1000).toISOString();
    server.use(http.get(FLEET, () => HttpResponse.json({
      tenants: [
        { tenant_id: "a", tenant_name: "Acme", enabled: 2, disabled: 0, revoked: 0,
          total_devices: 2, last_log_at: null, volume_24h: null },
        { tenant_id: "b", tenant_name: "Beta", enabled: 1, disabled: 1, revoked: 0,
          total_devices: 2, last_log_at: recent, volume_24h: 42 },
      ],
      totals: { tenants_with_forwarding: 2, enabled_devices: 3, volume_24h: 42, silent_tenants: 1 },
    })));
    renderWithProviders(<LogFleetPage />);
    expect(await screen.findByText("Acme")).toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();
    expect(screen.getByTestId("fleet-silent-count")).toHaveTextContent("1");
    expect(screen.getByTestId("fleet-silent-a")).toBeInTheDocument();
    expect(screen.queryByTestId("fleet-silent-b")).toBeNull();
  });
});
