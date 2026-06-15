import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { ConfigAuditCard } from "../ConfigAuditCard";
import { ConfigAuditTab } from "../ConfigAuditTab";
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

function event(
  category: string,
  name: string,
  src_ip: string,
  action: string,
  change_ref: string,
) {
  return {
    time: "2026-06-15T10:00:00Z",
    device_id: "d1",
    source: "config_audit",
    category,
    src_ip,
    dst_ip: "",
    name,
    severity: action === "api" ? "info" : "medium",
    action,
    attributes: { actor: name, channel: action, change_ref },
  };
}

describe("ConfigAuditTab", () => {
  it("renders config-change rows with a Direct badge for gui/system and none for api", async () => {
    server.use(
      http.get("/api/tenants/t1/events", () =>
        HttpResponse.json({
          items: [
            event("firewall", "admin", "10.0.0.5", "gui", "/firewall_rules.php"),
            event("monit", "root", "192.168.6.100", "api", "/api/monit/settings/delTest"),
            event("firmware", "root", "", "system", "/usr/local/opnsense/scripts/firmware/register.php"),
          ],
          next_cursor: null,
        }),
      ),
    );
    renderWithProviders(withTenant(<ConfigAuditTab deviceId="d1" />));

    // The change_ref column renders the request path of each change.
    expect(await screen.findByText("/firewall_rules.php")).toBeInTheDocument();
    expect(screen.getByText("/api/monit/settings/delTest")).toBeInTheDocument();

    // Two direct (gui + system) rows → two "Direct" badges; the api row gets none.
    const badges = screen.getAllByText("Direct");
    expect(badges).toHaveLength(2);
  });

  it("shows the empty state when there are no config changes", async () => {
    server.use(
      http.get("/api/tenants/t1/events", () =>
        HttpResponse.json({ items: [], next_cursor: null }),
      ),
    );
    renderWithProviders(withTenant(<ConfigAuditTab deviceId="d1" />));
    expect(await screen.findByText(/No config changes in this window/i)).toBeInTheDocument();
  });

  it("shows Load more when a next_cursor is present and pages on click", async () => {
    server.use(
      http.get("/api/tenants/t1/events", ({ request }) => {
        const after = new URL(request.url).searchParams.get("after");
        if (!after) {
          return HttpResponse.json({
            items: [event("firewall", "admin", "10.0.0.5", "gui", "/firewall_rules.php")],
            next_cursor: "CURSOR1",
          });
        }
        return HttpResponse.json({
          items: [event("interfaces", "root", "", "system", "/usr/local/opnsense/scripts/x.php")],
          next_cursor: null,
        });
      }),
    );
    renderWithProviders(withTenant(<ConfigAuditTab deviceId="d1" />));

    expect(await screen.findByText("/firewall_rules.php")).toBeInTheDocument();
    const loadMore = await screen.findByRole("button", { name: /Load more/i });
    await userEvent.click(loadMore);

    expect(await screen.findByText("/usr/local/opnsense/scripts/x.php")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /Load more/i })).not.toBeInTheDocument(),
    );
  });
});

describe("ConfigAuditCard", () => {
  it("renders ranked change-channel counts and flags direct channels", async () => {
    server.use(
      http.get("/api/tenants/t1/events/top", () =>
        HttpResponse.json([
          { value: "api", count: 8 },
          { value: "gui", count: 3 },
          { value: "system", count: 1 },
        ]),
      ),
    );
    renderWithProviders(withTenant(<ConfigAuditCard />));

    // api → no badge; gui + system → two "Direct" badges.
    expect(await screen.findByText("8")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getAllByText("Direct")).toHaveLength(2);
  });

  it("shows the empty state when there are no config changes", async () => {
    server.use(
      http.get("/api/tenants/t1/events/top", () => HttpResponse.json([])),
    );
    renderWithProviders(withTenant(<ConfigAuditCard />));
    expect(await screen.findByText(/No config changes in this window/i)).toBeInTheDocument();
  });
});
