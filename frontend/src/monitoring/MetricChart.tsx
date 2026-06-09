import { Card, Text } from "@mantine/core";
import { LineChart } from "@mantine/charts";
import type { MetricPoint } from "./types";

export interface ChartData {
  data: Record<string, number | string>[];
  series: string[];
}

/** Trasforma punti {time,label,value} in righe per timestamp con una colonna per label.
 *  Label vuota ('') → colonna 'value'. Funzione pura, testata a parte. */
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
}: {
  title: string;
  points: MetricPoint[];
  unit?: string;
}) {
  const { data, series } = toChartData(points);
  return (
    <Card withBorder padding="sm">
      <Text fw={600} size="sm" mb="xs">
        {title}
        {unit ? ` (${unit})` : ""}
      </Text>
      {data.length === 0 ? (
        <Text c="dimmed" size="sm">
          Nessun dato ancora
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
        />
      )}
    </Card>
  );
}
