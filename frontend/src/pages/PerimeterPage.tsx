import { useState } from "react";
import {
  Alert,
  Card,
  Group,
  Loader,
  SegmentedControl,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";

import { useLocale, useT } from "../i18n";
import { countryLabel } from "../perimeter/countryLabel";
import { usePerimeterAttackers, type PerimeterKind } from "../perimeter/perimeterHooks";

const WINDOWS: Record<string, number> = { "24h": 1, "7d": 7, "30d": 30 };

function PerimeterTable({ kind, windowDays }: { kind: PerimeterKind; windowDays: number }) {
  const t = useT();
  const { locale } = useLocale();
  const tp = t.perimeter;
  const tc = t.overview.attackerCountries;
  const { data, isLoading, error } = usePerimeterAttackers(kind, { windowDays, limit: 100 });
  const labelCol = kind === "login_failed" ? tp.user : tp.port;

  return (
    <Card withBorder padding="lg" radius="md" data-testid={`perimeter-table-${kind}`}>
      <Text fw={600}>{kind === "login_failed" ? tp.failedLogins : tp.firewallBlocks}</Text>
      <Text size="xs" c="dimmed">
        {kind === "login_failed" ? tp.failedLoginsHelp : tp.firewallBlocksHelp}
      </Text>
      {isLoading && <Loader size="sm" mt="md" />}
      {error && <Alert color="red" mt="md">{tp.loadError}</Alert>}
      {data && data.length === 0 && <Text size="sm" c="dimmed" mt="md">{tp.empty}</Text>}
      {data && data.length > 0 && (
        <Table mt="md" highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{tp.ip}</Table.Th><Table.Th>{tp.country}</Table.Th>
              <Table.Th>{labelCol}</Table.Th><Table.Th>{tp.count}</Table.Th><Table.Th>{tp.lastSeen}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {data.map((row) => (
              <Table.Tr key={row.src_ip} data-testid={`perimeter-row-${row.src_ip}`}>
                <Table.Td>{row.src_ip}</Table.Td>
                <Table.Td>{countryLabel(row.country, locale, tc.private, tc.unknown)}</Table.Td>
                <Table.Td>{row.label || "—"}</Table.Td>
                <Table.Td>{row.count}</Table.Td>
                <Table.Td>{new Date(row.last_seen).toLocaleString(locale)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Card>
  );
}

export function PerimeterPage() {
  const t = useT();
  const tp = t.perimeter;
  const [win, setWin] = useState("7d");
  const windowDays = WINDOWS[win] ?? 7;

  return (
    <Stack>
      <Group justify="space-between">
        <div>
          <Title order={3}>{tp.title}</Title>
          <Text size="sm" c="dimmed">{tp.subtitle}</Text>
        </div>
        <SegmentedControl
          value={win}
          onChange={setWin}
          data={Object.keys(WINDOWS)}
          data-testid="perimeter-window"
        />
      </Group>
      <PerimeterTable kind="login_failed" windowDays={windowDays} />
      <PerimeterTable kind="firewall_block" windowDays={windowDays} />
    </Stack>
  );
}
