import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { PerimeterCard } from "../PerimeterCard";
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

const ROW = {
  src_ip: "203.0.113.9",
  country: "RU",
  count: 7,
  last_seen: "2026-06-14T12:00:00Z",
  label: "23",
};

describe("PerimeterCard", () => {
  it("renders ranked attacker IPs with country + label + count", async () => {
    server.use(
      http.get("/api/tenants/t1/perimeter/attackers", () => HttpResponse.json([ROW])),
    );
    renderWithProviders(withTenant(<PerimeterCard kind="firewall_block" />));
    expect(await screen.findByText("203.0.113.9")).toBeInTheDocument();
    expect(screen.getByText(/Russia/)).toBeInTheDocument();  // RU via Intl.DisplayNames (en in tests)
    expect(screen.getByText("7")).toBeInTheDocument();
  });

  it("shows the empty state when there is no activity", async () => {
    server.use(
      http.get("/api/tenants/t1/perimeter/attackers", () => HttpResponse.json([])),
    );
    renderWithProviders(withTenant(<PerimeterCard kind="login_failed" />));
    expect(await screen.findByText(/No activity in this window/i)).toBeInTheDocument();
  });
});
