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
import { OpnsenseSettingForm } from "../OpnsenseSettingForm";

type SettingBody = { endpoint_key: string; payload: Record<string, string> };

const ENDPOINTS = [{ key: "ids_general", label: "IDS — General settings" }];

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

const INTROSPECT = {
  endpoint_key: "ids_general",
  label: "IDS — General settings",
  fields: [
    { path: "general.enabled", label: "enabled", control: "switch", value: "0" },
    {
      path: "general.mode",
      label: "mode",
      control: "select",
      options: [
        { value: "pcap", label: "PCAP" },
        { value: "netmap", label: "Netmap" },
      ],
      value: "pcap",
    },
  ],
};

// Capture the latest controlled value so the test can assert the payload after interactions.
let latest: SettingBody = { endpoint_key: "", payload: {} };

function Harness() {
  const [v, setV] = useState<SettingBody>({ endpoint_key: "", payload: {} });
  // Mirror the latest controlled value into a captured variable (in an effect, not
  // during render) so the test can assert the payload after interactions.
  useEffect(() => {
    latest = v;
  }, [v]);
  return <OpnsenseSettingForm value={v} onChange={setV} />;
}

/** Wrap with all providers the form needs, including TenantContext (activeId "t1"). */
function renderHarness() {
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
  return render(<Harness />, { wrapper: Wrapper });
}

function mockHappyPath() {
  server.use(
    http.get("/api/opnsense/setting-endpoints", () => HttpResponse.json(ENDPOINTS)),
    http.get("/api/tenants/t1/devices", () => HttpResponse.json(DEVICES)),
    http.get(
      "/api/tenants/t1/devices/d1/opnsense/settings/ids_general",
      () => HttpResponse.json(INTROSPECT),
    ),
  );
}

/** Drive the endpoint + device Selects and click Load, leaving the auto-form rendered. */
async function pickEndpointDeviceAndLoad() {
  // Pick the endpoint (Mantine Select: click the input, then click the option text).
  await userEvent.click(screen.getByTestId("setting-endpoint"));
  await userEvent.click(await screen.findByText("IDS — General settings"));

  // Pick the reference device.
  await userEvent.click(await screen.findByTestId("setting-device"));
  await userEvent.click(await screen.findByText("fw1"));

  // Load the fields.
  await userEvent.click(screen.getByTestId("setting-load"));
}

describe("OpnsenseSettingForm", () => {
  beforeEach(() => {
    latest = { endpoint_key: "", payload: {} };
  });

  it("introspect -> auto-form renders the right controls per field", async () => {
    mockHappyPath();
    renderHarness();

    await pickEndpointDeviceAndLoad();

    // The auto-generated controls appear with the inferred control types.
    const enabled = await screen.findByTestId("setting-field-general.enabled");
    const mode = await screen.findByTestId("setting-field-general.mode");
    expect(enabled).toBeInTheDocument();
    expect(mode).toBeInTheDocument();

    // The switch is a checkbox; the select is a text input (Mantine).
    expect(enabled).toHaveAttribute("type", "checkbox");

    // The payload was initialised from the fields' current device values.
    expect(latest.endpoint_key).toBe("ids_general");
    expect(latest.payload["general.enabled"]).toBe("0");
    expect(latest.payload["general.mode"]).toBe("pcap");
  });

  it("toggling a switch updates the payload via onChange", async () => {
    mockHappyPath();
    renderHarness();

    await pickEndpointDeviceAndLoad();

    const enabled = await screen.findByTestId("setting-field-general.enabled");
    expect(latest.payload["general.enabled"]).toBe("0");

    // Toggle the switch on -> payload flips to "1".
    await userEvent.click(enabled);
    expect(latest.payload["general.enabled"]).toBe("1");

    // The endpoint key is preserved alongside the toggled value.
    expect(latest.endpoint_key).toBe("ids_general");
  });
});
