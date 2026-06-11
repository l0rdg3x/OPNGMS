import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode, useEffect, useState } from "react";
import { MemoryRouter } from "react-router-dom";
import { server } from "../../test/server";
import { I18nProvider } from "../../i18n";
import { TenantContext } from "../../tenant/TenantProvider";
import { IdsRulesetForm } from "../IdsRulesetForm";

type IdsBody = { rulesets: string[] };

const DEVICES = [
  {
    id: "d1",
    name: "fw1",
    tenant_id: "t1",
    base_url: "https://x",
    verify_tls: true,
    tls_fingerprint: null,
    site: null,
    tags: [],
    status: "reachable",
    last_seen: null,
    firmware_version: null,
    created_at: "2026-06-11T00:00:00Z",
    updated_at: "2026-06-11T00:00:00Z",
  },
];

const RULESETS = [
  { filename: "a.rules", description: "Alpha", enabled: "0" },
  { filename: "b.rules", description: "Bravo", enabled: "1" },
];

// Capture the latest controlled value so the test can assert the body after interactions.
let latest: IdsBody = { rulesets: [] };

function Harness() {
  const [v, setV] = useState<IdsBody>({ rulesets: [] });
  // Mirror the latest controlled value into a captured variable (in an effect, not
  // during render) so the test can assert the body after interactions.
  useEffect(() => {
    latest = v;
  }, [v]);
  return <IdsRulesetForm value={v} onChange={setV} />;
}

/** Shared providers wrapper. */
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

/** Wrap with all providers the form needs, including TenantContext (activeId "t1"). */
function renderHarness() {
  return render(<Harness />, { wrapper: makeWrapper() });
}

function mockHappyPath() {
  server.use(
    http.get("/api/tenants/t1/devices", () => HttpResponse.json(DEVICES)),
    http.get(
      "/api/tenants/t1/devices/d1/opnsense/ids/rulesets",
      () => HttpResponse.json(RULESETS),
    ),
  );
}

/** Drive the device Select and click Load, leaving the MultiSelect rendered. */
async function pickDeviceAndLoad() {
  // Pick the reference device (Mantine Select: click the input, then click the option text).
  await userEvent.click(await screen.findByTestId("ids-device"));
  await userEvent.click(await screen.findByText("fw1"));

  // Load the rulesets.
  await userEvent.click(screen.getByTestId("ids-load"));
}

describe("IdsRulesetForm", () => {
  beforeEach(() => {
    latest = { rulesets: [] };
  });

  it("loads rulesets and reports the selection via onChange", async () => {
    mockHappyPath();
    renderHarness();

    await pickDeviceAndLoad();

    // The multi-select appears with the catalog.
    const rulesets = await screen.findByTestId("ids-rulesets");
    expect(rulesets).toBeInTheDocument();

    // Open it and pick "Alpha" -> the body reflects the filename, not the label.
    await userEvent.click(rulesets);
    await userEvent.click(await screen.findByText("Alpha"));

    expect(latest.rulesets).toEqual(["a.rules"]);
  });

  it("shows the load hint and hides the multi-select before loading", async () => {
    mockHappyPath();
    renderHarness();

    // Before any load, the hint is shown and the multi-select is absent.
    expect(await screen.findByTestId("ids-load-hint")).toBeInTheDocument();
    expect(screen.queryByTestId("ids-rulesets")).not.toBeInTheDocument();
  });

  it("shows the no-device message when the tenant has no devices", async () => {
    server.use(
      http.get("/api/tenants/t1/devices", () => HttpResponse.json([])),
    );
    renderHarness();

    expect(await screen.findByTestId("ids-no-device")).toBeInTheDocument();
  });
});
