import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { HealthSummaryCards } from "../HealthSummaryCards";

describe("HealthSummaryCards", () => {
  it("mostra totale device, conteggi per stato e alert attivi", () => {
    render(
      <MantineProvider>
        <HealthSummaryCards
          health={{ total_devices: 3, by_status: { reachable: 2, unverified: 1 }, active_alerts: 4 }}
        />
      </MantineProvider>,
    );
    expect(screen.getByText("3")).toBeInTheDocument(); // totale
    expect(screen.getByText(/reachable/i)).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument(); // alert attivi
  });
});
