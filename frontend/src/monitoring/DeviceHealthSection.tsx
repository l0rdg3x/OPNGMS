import { lazy, Suspense, useState } from "react";
import { Group, Loader, SegmentedControl, SimpleGrid, Stack, Title } from "@mantine/core";
import { useT } from "../i18n";
import { humanBytes } from "../utils/bytes";
import { useDeviceMetrics, useMetricLabels } from "./hooks";
import type { MetricPoint, Range } from "./types";

// MetricChart wraps @mantine/charts (recharts) — the bulk of the device page's JS. Lazy-load it so the
// page chunk stays small and recharts streams in on demand; the grid renders behind one Suspense.
const MetricChart = lazy(() => import("./MetricChart").then((m) => ({ default: m.MetricChart })));

function ChartFor({
  deviceId,
  metric,
  title,
  unit,
  valueFormatter,
  labelMap,
  range,
}: {
  deviceId: string;
  metric: string;
  title: string;
  unit?: string;
  valueFormatter?: (value: number) => string;
  labelMap?: Record<string, string>;
  range: Range;
}) {
  const q = useDeviceMetrics(deviceId, metric, range);
  const points = (q.data?.points ?? []) as MetricPoint[];
  return (
    <MetricChart
      title={title}
      points={points}
      unit={unit}
      valueFormatter={valueFormatter}
      labelMap={labelMap}
    />
  );
}

export function DeviceHealthSection({ deviceId }: { deviceId: string }) {
  const t = useT();
  const [range, setRange] = useState<Range>("24h");
  const labelMap = useMetricLabels(deviceId).data ?? {};
  return (
    <Stack>
      <Group justify="space-between">
        <Title order={4}>{t.deviceHealth.title}</Title>
        <SegmentedControl
          value={range}
          onChange={(v) => setRange(v as Range)}
          data={[
            { label: "1h", value: "1h" },
            { label: "24h", value: "24h" },
            { label: "7d", value: "7d" },
          ]}
        />
      </Group>
      <Suspense fallback={<Loader size="sm" />}>
      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <ChartFor deviceId={deviceId} metric="cpu.pct" title={t.deviceHealth.cpu} unit="%" range={range} />
        <ChartFor deviceId={deviceId} metric="mem.pct" title={t.deviceHealth.memory} unit="%" range={range} />
        <ChartFor deviceId={deviceId} metric="disk.pct" title={t.deviceHealth.disk} unit="%" range={range} />
        <ChartFor
          deviceId={deviceId}
          metric="iface.bytes_in"
          title={t.deviceHealth.trafficIn}
          valueFormatter={humanBytes}
          labelMap={labelMap}
          range={range}
        />
        <ChartFor
          deviceId={deviceId}
          metric="iface.bytes_out"
          title={t.deviceHealth.trafficOut}
          valueFormatter={humanBytes}
          labelMap={labelMap}
          range={range}
        />
        <ChartFor
          deviceId={deviceId}
          metric="gateway.rtt_ms"
          title={t.deviceHealth.gatewayRtt}
          unit="ms"
          labelMap={labelMap}
          range={range}
        />
        <ChartFor
          deviceId={deviceId}
          metric="gateway.loss_pct"
          title={t.deviceHealth.gatewayLoss}
          unit="%"
          labelMap={labelMap}
          range={range}
        />
        <ChartFor deviceId={deviceId} metric="vpn.up" title={t.deviceHealth.vpnUp} labelMap={labelMap} range={range} />
      </SimpleGrid>
      </Suspense>
    </Stack>
  );
}
