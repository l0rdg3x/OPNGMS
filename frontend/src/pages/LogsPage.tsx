import { useState } from "react";
import {
  Alert, Button, Card, Code, Group, Modal, Select, Stack, Table, Text, TextInput, Title,
} from "@mantine/core";
import { DateTimePicker } from "@mantine/dates";
import dayjs from "dayjs";

import { useTenant } from "../tenant/useTenant";
import { useTenantDevices } from "../templates/settingHooks";
import { useLogSearch, type LogSearchOut } from "../logs/logHooks";

const MANTINE_FMT = "YYYY-MM-DD HH:mm:ss";

/** A Mantine date-time string (local, "YYYY-MM-DD HH:mm:ss") for `daysAgo` days ago. */
function mantineDaysAgo(days: number): string {
  return dayjs().subtract(days, "day").format(MANTINE_FMT);
}

/** Parse a Mantine date-time string into an ISO date-time the API expects. */
function toIso(value: string): string {
  return dayjs(value).toISOString();
}

export function LogsPage() {
  const { activeId, tenants } = useTenant();
  const role = tenants.find((tn) => tn.id === activeId)?.role ?? null;
  const devices = useTenantDevices();
  const search = useLogSearch();
  const [query, setQuery] = useState("");
  const [deviceId, setDeviceId] = useState<string | null>(null);
  const [frm, setFrm] = useState<string | null>(mantineDaysAgo(1));
  const [to, setTo] = useState<string | null>(dayjs().format(MANTINE_FMT));
  const [result, setResult] = useState<LogSearchOut | null>(null);
  const [raw, setRaw] = useState<Record<string, unknown> | null>(null);

  if (role !== "tenant_admin" && role !== "operator") {
    return <Alert color="red" data-testid="logs-forbidden">Operators and tenant admins only.</Alert>;
  }

  const deviceName = (id: string) =>
    (devices.data ?? []).find((d) => d.id === id)?.name ?? id;

  async function run() {
    if (!frm || !to) return;
    const res = await search.mutateAsync({
      query, device_id: deviceId, frm: toIso(frm), to: toIso(to), page: 0, size: 100,
    });
    setResult(res);
  }

  return (
    <Stack>
      <Title order={3}>Logs</Title>
      <Card withBorder padding="md" radius="md">
        <Stack>
          <Group grow>
            <DateTimePicker label="From" value={frm} onChange={setFrm} data-testid="logs-from" />
            <DateTimePicker label="To" value={to} onChange={setTo} data-testid="logs-to" />
            <Select label="Device" clearable data={(devices.data ?? []).map((d) => ({ value: d.id, label: d.name }))}
                    value={deviceId} onChange={setDeviceId} data-testid="logs-device" />
          </Group>
          <TextInput label="Query (Lucene)" placeholder="e.g. action:block AND src_ip:10.0.0.1"
                     value={query} onChange={(e) => setQuery(e.currentTarget.value)} data-testid="logs-query" />
          <Group>
            <Button onClick={run} loading={search.isPending} data-testid="logs-search">Search</Button>
            {result && <Text size="sm" c="dimmed">{result.total} matches</Text>}
          </Group>
        </Stack>
      </Card>

      {search.isError && <Alert color="red">Log search failed.</Alert>}

      {result && (
        <Table highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Time</Table.Th><Table.Th>Device</Table.Th>
              <Table.Th>Program</Table.Th><Table.Th>Message</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {result.hits.map((h) => (
              <Table.Tr key={h.id} style={{ cursor: "pointer" }} onClick={() => setRaw(h.source)} data-testid={`logrow-${h.id}`}>
                <Table.Td>{h.timestamp}</Table.Td>
                <Table.Td>{deviceName(h.device_id)}</Table.Td>
                <Table.Td>{h.program}</Table.Td>
                <Table.Td>{h.message}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      <Modal opened={raw !== null} onClose={() => setRaw(null)} title="Raw document" size="lg">
        <Code block data-testid="logs-raw">{JSON.stringify(raw, null, 2)}</Code>
      </Modal>
    </Stack>
  );
}
