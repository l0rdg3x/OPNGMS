import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { ConfigTab } from "../ConfigTab";
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

const tree = {
  tag: "opnsense",
  path: "opnsense",
  attributes: {},
  value: null,
  sensitive: false,
  children: [
    {
      tag: "system",
      path: "opnsense/system",
      attributes: {},
      value: null,
      sensitive: false,
      children: [
        {
          tag: "hostname",
          path: "opnsense/system/hostname",
          attributes: {},
          value: "fw1",
          sensitive: false,
          children: [],
        },
        {
          tag: "password",
          path: "opnsense/system/password",
          attributes: {},
          value: null,
          sensitive: true,
          children: [],
        },
      ],
    },
  ],
};

const inv = {
  opnsense_version: "24.7.2",
  interfaces: [
    { name: "wan", nic: "igb0", description: "WAN" },
    { name: "lan", nic: "igb1", description: "LAN" },
  ],
  configured_sections: ["system", "interfaces", "filter"],
  available_capabilities: [
    { id: "os-wireguard", label: "WireGuard VPN", area: "vpn/wireguard" },
  ],
};

describe("ConfigTab", () => {
  it("renders the capabilities panel and the config tree", async () => {
    server.use(
      http.get("/api/tenants/t1/devices/d1/config/model", () =>
        HttpResponse.json(tree),
      ),
      http.get("/api/tenants/t1/devices/d1/config/capabilities", () =>
        HttpResponse.json(inv),
      ),
    );
    renderWithProviders(withTenant(<ConfigTab deviceId="d1" />));

    // capabilities panel: OPNsense version + a NIC
    expect(await screen.findByText("24.7.2")).toBeInTheDocument();
    expect(screen.getByText("igb0")).toBeInTheDocument();
    // config tree: a non-sensitive leaf value
    expect(screen.getByText("fw1")).toBeInTheDocument();
    // no secret string ever appears in the DOM
    expect(document.body.textContent).not.toContain("password-secret");
  });

  it("shows the empty state when there is no snapshot yet (404)", async () => {
    server.use(
      http.get(
        "/api/tenants/t1/devices/d1/config/model",
        () => new HttpResponse(null, { status: 404 }),
      ),
      http.get(
        "/api/tenants/t1/devices/d1/config/capabilities",
        () => new HttpResponse(null, { status: 404 }),
      ),
    );
    renderWithProviders(withTenant(<ConfigTab deviceId="d1" />));

    expect(
      await screen.findByText(/No configuration captured yet/i),
    ).toBeInTheDocument();
  });
});
