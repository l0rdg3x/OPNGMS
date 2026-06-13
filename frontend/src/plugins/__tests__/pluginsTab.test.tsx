import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { PluginsTab } from "../PluginsTab";
import { TenantContext } from "../../tenant/TenantProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

const PLUGINS = "/api/tenants/t1/devices/d1/plugins";
const ACTION = "/api/tenants/t1/devices/d1/firmware/action";

function withTenant(node: ReactNode, role = "tenant_admin") {
  return (
    <TenantContext.Provider
      value={{ tenants: [{ id: "t1", name: "A", slug: "a", role }], activeId: "t1",
               setActiveId: () => {}, loading: false }}>
      {node}
    </TenantContext.Provider>
  );
}

const SAMPLE = [
  { name: "os-wireguard", installed: true, version: "2.6", locked: false },
  { name: "os-acme-client", installed: false, version: "4.16", locked: false },
];

describe("PluginsTab", () => {
  it("lists plugins and badges install state", async () => {
    server.use(http.get(PLUGINS, () => HttpResponse.json(SAMPLE)));
    renderWithProviders(withTenant(<PluginsTab deviceId="d1" />));
    expect(await screen.findByText("wireguard")).toBeInTheDocument();
    expect(screen.getByText("acme-client")).toBeInTheDocument();
    expect(screen.getByTestId("plugin-remove-os-wireguard")).toBeInTheDocument();   // installed -> Remove
    expect(screen.getByTestId("plugin-install-os-acme-client")).toBeInTheDocument(); // available -> Install
  });

  it("install triggers a plugin_install firmware action", async () => {
    server.use(http.get(PLUGINS, () => HttpResponse.json(SAMPLE)));
    let posted: { kind?: string; target?: string } = {};
    server.use(http.post(ACTION, async ({ request }) => {
      posted = (await request.json()) as { kind?: string; target?: string };
      return HttpResponse.json({ id: "a1", kind: posted.kind, target: posted.target, status: "scheduled",
        result: {}, created_at: "2026-06-13T00:00:00Z", scheduled_at: null });
    }));
    renderWithProviders(withTenant(<PluginsTab deviceId="d1" />));
    await userEvent.click(await screen.findByTestId("plugin-install-os-acme-client"));
    await userEvent.click(await screen.findByTestId("plugin-confirm"));
    await waitFor(() => expect(posted).toEqual({ kind: "plugin_install", target: "os-acme-client" }));
  });

  it("hides write buttons for a read-only role", async () => {
    server.use(http.get(PLUGINS, () => HttpResponse.json(SAMPLE)));
    renderWithProviders(withTenant(<PluginsTab deviceId="d1" />, "read_only"));
    expect(await screen.findByText("wireguard")).toBeInTheDocument();
    expect(screen.queryByTestId("plugin-remove-os-wireguard")).not.toBeInTheDocument();
  });

  it("closes the modal even when the action fails (no stuck UI)", async () => {
    server.use(http.get(PLUGINS, () => HttpResponse.json(SAMPLE)));
    server.use(http.post(ACTION, () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    renderWithProviders(withTenant(<PluginsTab deviceId="d1" />));
    await userEvent.click(await screen.findByTestId("plugin-install-os-acme-client"));
    await userEvent.click(await screen.findByTestId("plugin-confirm"));
    // The `finally` closes the modal on error, so the confirm button must disappear.
    await waitFor(() => expect(screen.queryByTestId("plugin-confirm")).not.toBeInTheDocument());
  });

  it("shows Configure for an installed plugin with a config model and opens the drawer", async () => {
    server.use(
      http.get(PLUGINS, () => HttpResponse.json(SAMPLE)),
      http.get("/api/tenants/t1/devices/d1/plugin-models",
        () => HttpResponse.json([{ package: "os-wireguard", model_id: "wireguard", title: "WireGuard" }])),
      http.get("/api/tenants/t1/devices/d1/catalog/models/wireguard", () => HttpResponse.json({
        model: { id: "wireguard", title: "WireGuard", fields: [], grids: [], pages: [], endpoints: {} },
        values: {}, grids: {}, field_options: {}, grid_field_options: {}, reachable: true, read_only: false })),
    );
    renderWithProviders(withTenant(<PluginsTab deviceId="d1" />));
    // os-wireguard is installed + has a model -> Configure shows; os-acme-client (not installed) does not.
    const cfg = await screen.findByTestId("plugin-configure-os-wireguard");
    expect(screen.queryByTestId("plugin-configure-os-acme-client")).not.toBeInTheDocument();
    await userEvent.click(cfg);
    expect(await screen.findByText("WireGuard")).toBeInTheDocument();   // the form title renders in the drawer
  });
});
