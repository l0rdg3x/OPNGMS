import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { HealthSummaryCards } from "../HealthSummaryCards";

describe("HealthSummaryCards", () => {
  it("shows total devices, per-status counts and active alerts", () => {
    render(
      <MantineProvider>
        <HealthSummaryCards
          health={{ total_devices: 3, by_status: { reachable: 2, unverified: 1 }, active_alerts: 4 }}
        />
      </MantineProvider>,
    );
    expect(screen.getByText("3")).toBeInTheDocument(); // total
    expect(screen.getByText(/reachable/i)).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument(); // active alerts
  });
});
