import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { AlertsPage } from "../AlertsPage";
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

const active = {
  id: "a1", device_id: "d1", type: "device.down", label: "", severity: "critical",
  opened_at: "2026-06-09T10:00:00Z", resolved_at: null, details: {},
};
const resolved = {
  id: "a2", device_id: "d1", type: "gateway.down", label: "wan", severity: "warning",
  opened_at: "2026-06-08T10:00:00Z", resolved_at: "2026-06-08T11:00:00Z", details: {},
};

describe("AlertsPage", () => {
  it("filters active vs history", async () => {
    server.use(
      http.get("/api/tenants/t1/alerts", ({ request }) => {
        const url = new URL(request.url);
        const a = url.searchParams.get("active");
        return HttpResponse.json(a === "false" ? [active, resolved] : [active]);
      }),
    );
    renderWithProviders(withTenant(<AlertsPage />));
    // default: active only
    expect(await screen.findByText("device.down")).toBeInTheDocument();
    expect(screen.queryByText("gateway.down")).not.toBeInTheDocument();
    // switch to history — Mantine SegmentedControl renders radio input + label;
    // the "History" text lives in a <span> inside the label, so click the text.
    await userEvent.click(screen.getByText(/history/i));
    await waitFor(() => expect(screen.getByText("gateway.down")).toBeInTheDocument());
  });
});
