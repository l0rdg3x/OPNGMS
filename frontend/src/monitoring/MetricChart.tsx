import { Card, Text } from "@mantine/core";
import { LineChart } from "@mantine/charts";
import { useT } from "../i18n";
import type { MetricPoint } from "./types";

export interface ChartData {
  data: Record<string, number | string>[];
  series: string[];
}

/** Turns {time,label,value} points into rows keyed by timestamp, one column per label.
 *  An empty label ('') maps to the 'value' column. Pure function, tested separately. */
// eslint-disable-next-line react-refresh/only-export-components
export function toChartData(points: MetricPoint[]): ChartData {
  const seriesSet: string[] = [];
  const byTime = new Map<string, Record<string, number | string>>();
  for (const p of points) {
    const key = p.label === "" ? "value" : p.label;
    if (!seriesSet.includes(key)) seriesSet.push(key);
    let row = byTime.get(p.time);
    if (!row) {
      row = { time: p.time };
      byTime.set(p.time, row);
    }
    row[key] = p.value;
  }
  return { data: Array.from(byTime.values()), series: seriesSet };
}

const PALETTE = ["blue.6", "teal.6", "orange.6", "grape.6", "red.6", "cyan.6"];

export function MetricChart({
  title,
  points,
  unit,
  valueFormatter,
}: {
  title: string;
  points: MetricPoint[];
  unit?: string;
  /** Formats Y-axis ticks + tooltip values (e.g. dynamic byte units). When set, the static
   *  `(unit)` title suffix is dropped since the values carry their own units. */
  valueFormatter?: (value: number) => string;
}) {
  const t = useT();
  const { data, series } = toChartData(points);
  return (
    <Card withBorder padding="sm">
      <Text fw={600} size="sm" mb="xs">
        {title}
        {unit && !valueFormatter ? ` (${unit})` : ""}
      </Text>
      {data.length === 0 ? (
        <Text c="dimmed" size="sm">
          {t.chart.noData}
        </Text>
      ) : (
        <LineChart
          h={200}
          data={data}
          dataKey="time"
          series={series.map((name, i) => ({ name, color: PALETTE[i % PALETTE.length] }))}
          curveType="monotone"
          withDots={false}
          tickLine="x"
          // Multi-line charts (per interface / gateway / VPN tunnel) need a legend to tell the
          // lines apart; a single 'value' series doesn't (the title already names it).
          withLegend={series.length > 1}
          valueFormatter={valueFormatter}
          yAxisProps={valueFormatter ? { tickFormatter: valueFormatter, width: 64 } : undefined}
        />
      )}
    </Card>
  );
}
