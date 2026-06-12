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

describe("LogsPage", () => {
  it("runs a search and shows results + raw doc modal", async () => {
    let body: unknown = null;
    server.use(
      http.get(DEVICES, () => HttpResponse.json([{ id: "d1", name: "fw-1" }])),
      http.post(SEARCH, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ total: 1, hits: [{ id: "h1", timestamp: "2026-06-01T00:00:00Z",
          device_id: "d1", host: "fw", program: "filterlog", message: "blocked 1.2.3.4", source: { a: 1 } }] });
      }),
    );
    renderWithProviders(withTenant(<LogsPage />, "operator"));
    await userEvent.type(await screen.findByTestId("logs-query"), "action:block");
    await userEvent.click(screen.getByTestId("logs-search"));
    await waitFor(() => expect((body as { query: string }).query).toBe("action:block"));
    expect(await screen.findByText(/blocked 1.2.3.4/)).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("logrow-h1"));
    expect(await screen.findByTestId("logs-raw")).toBeInTheDocument();
  });

  it("blocks read_only", () => {
    server.use(http.get(DEVICES, () => HttpResponse.json([])));
    renderWithProviders(withTenant(<LogsPage />, "read_only"));
    expect(screen.getByTestId("logs-forbidden")).toBeInTheDocument();
  });
});
