import { describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { server } from "../../test/server";
import { I18nProvider } from "../../i18n";
import { TenantContext } from "../../tenant/TenantProvider";
import { IdsPolicyForm, type PolicyBody } from "../IdsPolicyForm";

const EMPTY: PolicyBody = {
  description: "",
  enabled: "1",
  prio: "0",
  action: [],
  rulesets: [],
  content: {},
  new_action: "alert",
};

/** Shared providers wrapper (tenant context is needed by useTenantDevices). */
function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <I18nProvider>
        <MantineProvider>
          <QueryClientProvider client={qc}>
            <TenantContext.Provider
              value={{
                tenants: [{ id: "t1", name: "Tenant A", slug: "a", role: "tenant_admin" }],
                activeId: "t1",
                setActiveId: () => {},
                loading: false,
              }}
            >
              <MemoryRouter>{children}</MemoryRouter>
            </TenantContext.Provider>
          </QueryClientProvider>
        </MantineProvider>
      </I18nProvider>
    );
  }
  return Wrapper;
}

describe("IdsPolicyForm", () => {
  it("renders the policy fields by data-testid", async () => {
    server.use(http.get("/api/tenants/t1/devices", () => HttpResponse.json([])));
    render(<IdsPolicyForm value={EMPTY} onChange={vi.fn()} />, { wrapper: makeWrapper() });

    expect(await screen.findByTestId("idspolicy-description")).toBeInTheDocument();
    expect(screen.getByTestId("idspolicy-enabled")).toBeInTheDocument();
    expect(screen.getByTestId("idspolicy-prio")).toBeInTheDocument();
    expect(screen.getByTestId("idspolicy-action")).toBeInTheDocument();
    expect(screen.getByTestId("idspolicy-newaction")).toBeInTheDocument();
    expect(screen.getByTestId("idspolicy-content")).toBeInTheDocument();
  });

  it("calls onChange with the updated body when the description is typed", async () => {
    server.use(http.get("/api/tenants/t1/devices", () => HttpResponse.json([])));
    const onChange = vi.fn();
    render(<IdsPolicyForm value={EMPTY} onChange={onChange} />, { wrapper: makeWrapper() });

    await userEvent.type(await screen.findByTestId("idspolicy-description"), "D");

    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ description: "D" }),
    );
  });
});
