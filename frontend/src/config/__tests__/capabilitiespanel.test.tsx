import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { describe, expect, it } from "vitest";
import { I18nProvider } from "../../i18n";
import { CapabilitiesPanel } from "../CapabilitiesPanel";

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

function wrap(ui: React.ReactNode) {
  return (
    <MantineProvider>
      <I18nProvider>{ui}</I18nProvider>
    </MantineProvider>
  );
}

describe("CapabilitiesPanel", () => {
  it("shows version, interfaces, sections and available capabilities", () => {
    render(wrap(<CapabilitiesPanel inv={inv} />));
    expect(screen.getByText("24.7.2")).toBeInTheDocument();
    expect(screen.getByText("igb0")).toBeInTheDocument();
    expect(screen.getByText(/WireGuard VPN/)).toBeInTheDocument();
    expect(screen.getByText("filter")).toBeInTheDocument();
  });
});
