export type Range = "1h" | "24h" | "7d";

// Forma di un punto serie come restituito da GET .../metrics (vedi MetricPoint backend).
export interface MetricPoint {
  time: string;
  label: string;
  value: number;
}
