export type Range = "1h" | "24h" | "7d";

// Shape of a series point as returned by GET .../metrics (see MetricPoint on the backend).
export interface MetricPoint {
  time: string;
  label: string;
  value: number;
}
