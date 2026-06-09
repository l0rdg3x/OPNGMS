import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MetricChart, toChartData } from "../MetricChart";
import type { MetricPoint } from "../types";

const points: MetricPoint[] = [
  { time: "2026-06-09T12:00:00Z", label: "", value: 10 },
  { time: "2026-06-09T12:05:00Z", label: "", value: 20 },
];

describe("toChartData", () => {
  it("raggruppa per timestamp con una serie per label", () => {
    const multi: MetricPoint[] = [
      { time: "t1", label: "igb0", value: 1 },
      { time: "t1", label: "igb1", value: 2 },
      { time: "t2", label: "igb0", value: 3 },
    ];
    const { data, series } = toChartData(multi);
    expect(series).toEqual(["igb0", "igb1"]);
    expect(data).toEqual([
      { time: "t1", igb0: 1, igb1: 2 },
      { time: "t2", igb0: 3 },
    ]);
  });

  it("label vuota → serie 'value'", () => {
    const { series } = toChartData(points);
    expect(series).toEqual(["value"]);
  });
});

describe("MetricChart", () => {
  it("mostra il titolo e non crasha con dati", () => {
    render(
      <MantineProvider>
        <MetricChart title="CPU %" points={points} />
      </MantineProvider>,
    );
    expect(screen.getByText("CPU %")).toBeInTheDocument();
  });

  it("mostra empty-state senza dati", () => {
    render(
      <MantineProvider>
        <MetricChart title="CPU %" points={[]} />
      </MantineProvider>,
    );
    expect(screen.getByText(/nessun dato/i)).toBeInTheDocument();
  });
});
