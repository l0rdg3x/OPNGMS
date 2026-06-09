import { useState } from "react";
import { Group, SegmentedControl, SimpleGrid, Stack, Title } from "@mantine/core";
import { useT } from "../i18n";
import { MetricChart } from "./MetricChart";
import { useDeviceMetrics } from "./hooks";
import type { MetricPoint, Range } from "./types";

function ChartFor({
  deviceId,
  metric,
  title,
  unit,
  range,
}: {
  deviceId: string;
  metric: string;
  title: string;
  unit?: string;
  range: Range;
}) {
  const q = useDeviceMetrics(deviceId, metric, range);
  const points = (q.data?.points ?? []) as MetricPoint[];
  return <MetricChart title={title} points={points} unit={unit} />;
}

export function DeviceHealthSection({ deviceId }: { deviceId: string }) {
  const t = useT();
  const [range, setRange] = useState<Range>("24h");
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
      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <ChartFor deviceId={deviceId} metric="cpu.pct" title={t.deviceHealth.cpu} unit="%" range={range} />
        <ChartFor deviceId={deviceId} metric="mem.pct" title={t.deviceHealth.memory} unit="%" range={range} />
        <ChartFor deviceId={deviceId} metric="disk.pct" title={t.deviceHealth.disk} unit="%" range={range} />
        <ChartFor
          deviceId={deviceId}
          metric="iface.bytes_in"
          title={t.deviceHealth.trafficIn}
          unit="bytes"
          range={range}
        />
        <ChartFor
          deviceId={deviceId}
          metric="iface.bytes_out"
          title={t.deviceHealth.trafficOut}
          unit="bytes"
          range={range}
        />
        <ChartFor
          deviceId={deviceId}
          metric="gateway.rtt_ms"
          title={t.deviceHealth.gatewayRtt}
          unit="ms"
          range={range}
        />
        <ChartFor
          deviceId={deviceId}
          metric="gateway.loss_pct"
          title={t.deviceHealth.gatewayLoss}
          unit="%"
          range={range}
        />
        <ChartFor deviceId={deviceId} metric="vpn.up" title={t.deviceHealth.vpnUp} range={range} />
      </SimpleGrid>
    </Stack>
  );
}
