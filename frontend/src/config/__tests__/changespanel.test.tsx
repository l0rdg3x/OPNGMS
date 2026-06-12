import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { ChangesPanel } from "../ChangesPanel";
import { TenantContext } from "../../tenant/TenantProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

function withTenant(node: ReactNode, role: string = "tenant_admin") {
  return (
    <TenantContext.Provider
      value={{
        tenants: [{ id: "t1", name: "A", slug: "a", role }],
        activeId: "t1",
        setActiveId: () => {},
        loading: false,
      }}
    >
      {node}
    </TenantContext.Provider>
  );
}

const change = {
  id: "c1",
  device_id: "d1",
  kind: "alias",
  operation: "set",
  target: "web_servers",
  status: "draft",
  scheduled_at: null,
  applied_at: null,
  created_at: "2026-06-10T10:00:00Z",
  revertible: false,
  reverts_change_id: null,
};

const appliedRevertible = {
  ...change,
  status: "applied",
  applied_at: "2026-06-11T09:00:00Z",
  revertible: true,
  reverts_change_id: null,
};

const CHANGES_URL = "/api/tenants/t1/devices/d1/config/changes";
const REVERT_URL = "/api/tenants/t1/devices/d1/config/changes/c1/revert";
const DRIFT_URL = "/api/tenants/t1/devices/d1/config/drift-check";

const applied = {
  ...change,
  status: "applied",
  applied_at: "2026-06-11T09:00:00Z",
};

describe("ChangesPanel", () => {
  it("renders the change rows with a status badge and a propose button (tenant_admin)", async () => {
    server.use(http.get(CHANGES_URL, () => HttpResponse.json([change])));
    renderWithProviders(withTenant(<ChangesPanel deviceId="d1" />, "tenant_admin"));

    expect(await screen.findByText("web_servers")).toBeInTheDocument();
    expect(screen.getByText("alias")).toBeInTheDocument();
    // status badge
    expect(screen.getByText("draft")).toBeInTheDocument();
    // scheduled column header is always present
    expect(screen.getByText("Scheduled")).toBeInTheDocument();
    // propose button visible for an editor role
    expect(
      screen.getByRole("button", { name: /propose alias change/i }),
    ).toBeInTheDocument();
  });

  it("shows the error alert when the changes endpoint returns a server error", async () => {
    server.use(
      http.get(CHANGES_URL, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    renderWithProviders(withTenant(<ChangesPanel deviceId="d1" />, "tenant_admin"));

    expect(await screen.findByText("Failed to load changes")).toBeInTheDocument();
  });

  it("hides the propose button for a read_only tenant", async () => {
    server.use(http.get(CHANGES_URL, () => HttpResponse.json([change])));
    renderWithProviders(withTenant(<ChangesPanel deviceId="d1" />, "read_only"));

    expect(await screen.findByText("web_servers")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /propose alias change/i }),
    ).not.toBeInTheDocument();
  });

  it("shows the empty-state when there are no changes", async () => {
    server.use(http.get(CHANGES_URL, () => HttpResponse.json([])));
    renderWithProviders(withTenant(<ChangesPanel deviceId="d1" />, "tenant_admin"));

    expect(await screen.findByText(/no pending changes/i)).toBeInTheDocument();
  });

  it("shows a Revert control for an applied revertible change and POSTs on click", async () => {
    let revertCalled = false;
    server.use(
      http.get(CHANGES_URL, () => HttpResponse.json([appliedRevertible])),
      http.post(REVERT_URL, () => {
        revertCalled = true;
        return HttpResponse.json({
          ...appliedRevertible,
          id: "c2",
          status: "draft",
          revertible: false,
          reverts_change_id: "c1",
        });
      }),
    );

    renderWithProviders(withTenant(<ChangesPanel deviceId="d1" />, "tenant_admin"));

    const revertBtn = await screen.findByTestId("revert-c1");
    await userEvent.click(revertBtn);

    await waitFor(() => {
      expect(revertCalled).toBe(true);
    });
  });

  it("shows a drift badge after clicking Check drift", async () => {
    server.use(
      http.get(CHANGES_URL, () => HttpResponse.json([applied])),
      http.get(DRIFT_URL, () =>
        HttpResponse.json({
          reachable: true,
          checked_at: "2026-06-12T10:00:00Z",
          results: [
            {
              change_id: "c1",
              kind: "alias",
              target: "web_servers",
              status: "drifted",
              drifted_fields: ["content"],
            },
          ],
          unsupported_kinds: [],
        }),
      ),
    );
    renderWithProviders(withTenant(<ChangesPanel deviceId="d1" />, "tenant_admin"));

    await screen.findByText("web_servers");
    await userEvent.click(screen.getByRole("button", { name: /check drift/i }));

    expect(await screen.findByText("drift")).toBeInTheDocument();
  });

  it("shows the unreachable banner when the drift probe cannot reach the device", async () => {
    server.use(
      http.get(CHANGES_URL, () => HttpResponse.json([applied])),
      http.get(DRIFT_URL, () =>
        HttpResponse.json({
          reachable: false,
          checked_at: "2026-06-12T10:00:00Z",
          results: [],
          unsupported_kinds: [],
        }),
      ),
    );
    renderWithProviders(withTenant(<ChangesPanel deviceId="d1" />, "tenant_admin"));

    await screen.findByText("web_servers");
    await userEvent.click(screen.getByRole("button", { name: /check drift/i }));

    expect(await screen.findByText(/could not reach the device/i)).toBeInTheDocument();
  });

  it("renders a reverts-#chain badge when reverts_change_id is set", async () => {
    const inverse = {
      ...change,
      id: "c2",
      status: "draft",
      reverts_change_id: "c1abc234",
    };
    server.use(http.get(CHANGES_URL, () => HttpResponse.json([inverse])));
    renderWithProviders(withTenant(<ChangesPanel deviceId="d1" />, "tenant_admin"));

    expect(await screen.findByText(/reverts #c1abc23/i)).toBeInTheDocument();
  });
});
