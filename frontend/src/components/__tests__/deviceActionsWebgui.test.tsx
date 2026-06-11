import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { DeviceActions } from "../DeviceActions";
import { renderWithProviders } from "../../test/utils";

describe("DeviceActions WebGUI link", () => {
  it("renders an Open WebGUI link to the device base_url in a new tab", () => {
    renderWithProviders(
      <DeviceActions tenantId="t1" deviceId="d1" baseUrl="https://192.168.1.82" />,
    );
    const link = screen.getByTestId("btn-webgui") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("https://192.168.1.82");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
  });

  it("omits the WebGUI link when no base_url is given", () => {
    renderWithProviders(<DeviceActions tenantId="t1" deviceId="d1" />);
    expect(screen.queryByTestId("btn-webgui")).toBeNull();
  });
});
