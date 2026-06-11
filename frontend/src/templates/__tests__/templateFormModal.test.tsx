import { describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { server } from "../../test/server";
import { I18nProvider } from "../../i18n";
import { TenantContext } from "../../tenant/TenantProvider";
import { TemplateFormModal } from "../TemplateFormModal";

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

const RULESETS = [{ filename: "a.rules", description: "Alpha", enabled: "0" }];

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

function renderModal() {
  return render(
    <TemplateFormModal opened={true} onClose={vi.fn()} editing={null} />,
    { wrapper: makeWrapper() },
  );
}

describe("TemplateFormModal — suricata_ruleset", () => {
  it("creates a suricata_ruleset template with the selected rulesets", async () => {
    const capture = vi.fn();
    server.use(
      http.get("/api/tenants/t1/devices", () => HttpResponse.json(DEVICES)),
      http.get(
        "/api/tenants/t1/devices/d1/opnsense/ids/rulesets",
        () => HttpResponse.json(RULESETS),
      ),
      http.post("/api/templates", async ({ request }) => {
        capture(await request.json());
        return HttpResponse.json(
          { id: "x", kind: "suricata_ruleset", name: "Baseline IDS", version: 1 },
          { status: 201 },
        );
      }),
    );

    renderModal();

    // Name.
    await userEvent.type(screen.getByTestId("tpl-name"), "Baseline IDS");

    // Switch the kind to Suricata/IDS rulesets (Mantine Select: click input, then option).
    await userEvent.click(screen.getByTestId("tpl-kind"));
    await userEvent.click(await screen.findByText("Suricata/IDS rulesets"));

    // The IDS form is now shown: pick device + load + pick a ruleset.
    await userEvent.click(await screen.findByTestId("ids-device"));
    await userEvent.click(await screen.findByText("fw1"));
    await userEvent.click(screen.getByTestId("ids-load"));

    const rulesets = await screen.findByTestId("ids-rulesets");
    await userEvent.click(rulesets);
    await userEvent.click(await screen.findByText("Alpha"));

    // Save and assert the captured POST body.
    await userEvent.click(screen.getByTestId("tpl-save"));

    await waitFor(() =>
      expect(capture).toHaveBeenCalledWith({
        kind: "suricata_ruleset",
        name: "Baseline IDS",
        description: "",
        body: { rulesets: ["a.rules"] },
      })
    );
  });
});
