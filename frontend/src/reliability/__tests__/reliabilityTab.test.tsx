import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { ReliabilityCard } from "../ReliabilityCard";
import { ReliabilityTab } from "../ReliabilityTab";
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

function event(name: string, category: string, severity: string, message: string) {
  return {
    time: "2026-06-15T10:00:00Z",
    device_id: "d1",
    source: "service",
    category,
    src_ip: "",
    dst_ip: "",
    name,
    severity,
    action: "",
    attributes: { process: "kernel", message, log_severity: "crit" },
  };
}

describe("ReliabilityTab", () => {
  it("renders the device's service events as timeline rows", async () => {
    server.use(
      http.get("/api/tenants/t1/events", () =>
        HttpResponse.json({
          items: [event("service_crashed", "service", "high", "suricata exited on signal 11")],
          next_cursor: null,
        }),
      ),
    );
    renderWithProviders(withTenant(<ReliabilityTab deviceId="d1" />));

    expect(await screen.findByText("service_crashed")).toBeInTheDocument();
    expect(screen.getByText(/suricata exited on signal 11/)).toBeInTheDocument();
    // high severity → red badge label
    expect(screen.getByText("High")).toBeInTheDocument();
  });

  it("shows the empty state when there are no service events", async () => {
    server.use(
      http.get("/api/tenants/t1/events", () =>
        HttpResponse.json({ items: [], next_cursor: null }),
      ),
    );
    renderWithProviders(withTenant(<ReliabilityTab deviceId="d1" />));
    expect(await screen.findByText(/No service events in this window/i)).toBeInTheDocument();
  });

  it("shows Load more when a next_cursor is present and pages on click", async () => {
    server.use(
      http.get("/api/tenants/t1/events", ({ request }) => {
        const after = new URL(request.url).searchParams.get("after");
        if (!after) {
          return HttpResponse.json({
            items: [event("reboot", "reboot", "high", "reboot by root")],
            next_cursor: "CURSOR1",
          });
        }
        return HttpResponse.json({
          items: [event("filesystem_full", "disk", "high", "/var: filesystem full")],
          next_cursor: null,
        });
      }),
    );
    renderWithProviders(withTenant(<ReliabilityTab deviceId="d1" />));

    expect(await screen.findByText("reboot")).toBeInTheDocument();
    const loadMore = await screen.findByRole("button", { name: /Load more/i });
    await userEvent.click(loadMore);

    expect(await screen.findByText("filesystem_full")).toBeInTheDocument();
    // second page had no cursor → button gone
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /Load more/i })).not.toBeInTheDocument(),
    );
  });
});

describe("ReliabilityCard", () => {
  it("renders ranked service-event counts from /events/top", async () => {
    server.use(
      http.get("/api/tenants/t1/events/top", () =>
        HttpResponse.json([
          { value: "reboot", count: 3 },
          { value: "service_crashed", count: 1 },
        ]),
      ),
    );
    renderWithProviders(withTenant(<ReliabilityCard />));

    expect(await screen.findByText("reboot")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("service_crashed")).toBeInTheDocument();
  });

  it("shows the empty state when there are no service events", async () => {
    server.use(
      http.get("/api/tenants/t1/events/top", () => HttpResponse.json([])),
    );
    renderWithProviders(withTenant(<ReliabilityCard />));
    expect(await screen.findByText(/No service events in this window/i)).toBeInTheDocument();
  });
});
