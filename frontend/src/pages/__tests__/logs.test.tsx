import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { LogsPage } from "../LogsPage";
import { TenantContext } from "../../tenant/TenantProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

function withTenant(node: ReactNode, role: string = "operator") {
  return (
    <TenantContext.Provider value={{
      tenants: [{ id: "t1", name: "Acme", slug: "acme", role }],
      activeId: "t1", setActiveId: () => {}, loading: false,
    }}>{node}</TenantContext.Provider>
  );
}

const SEARCH = "http://localhost:3000/api/tenants/t1/logs/search";
const DEVICES = "http://localhost:3000/api/tenants/t1/devices";

function hit(id: string, msg: string) {
  return { id, timestamp: "2026-06-01T00:00:00Z", device_id: "d1", host: "fw",
           program: "filterlog", message: msg, source: { a: id } };
}

describe("LogsPage", () => {
  it("searches, loads more (appends), then exhausts the cursor", async () => {
    const bodies: Array<{ cursor?: unknown }> = [];
    let call = 0;
    server.use(
      http.get(DEVICES, () => HttpResponse.json([{ id: "d1", name: "fw-1" }])),
      http.post(SEARCH, async ({ request }) => {
        bodies.push((await request.json()) as { cursor?: unknown });
        call += 1;
        if (call === 1) {
          return HttpResponse.json({ total: 2, hits: [hit("h1", "first")],
            next_cursor: { pit_id: "P", after: [1, 1] } });
        }
        return HttpResponse.json({ total: 2, hits: [hit("h2", "second")], next_cursor: null });
      }),
    );
    renderWithProviders(withTenant(<LogsPage />, "operator"));
    await userEvent.click(await screen.findByTestId("logs-search"));
    expect(await screen.findByText(/first/)).toBeInTheDocument();
    await userEvent.click(await screen.findByTestId("logs-loadmore"));
    expect(await screen.findByText(/second/)).toBeInTheDocument();
    expect(screen.getByText(/first/)).toBeInTheDocument();
    await waitFor(() => expect((bodies[1] as { cursor?: unknown }).cursor).toEqual({ pit_id: "P", after: [1, 1] }));
    expect(screen.queryByTestId("logs-loadmore")).toBeNull();
  });

  it("blocks read_only", () => {
    server.use(http.get(DEVICES, () => HttpResponse.json([])));
    renderWithProviders(withTenant(<LogsPage />, "read_only"));
    expect(screen.getByTestId("logs-forbidden")).toBeInTheDocument();
  });
});
