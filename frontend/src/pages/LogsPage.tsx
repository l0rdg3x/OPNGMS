import { useState } from "react";
import {
  Alert, Button, Card, Code, Group, Modal, Select, Stack, Table, Text, TextInput, Title,
} from "@mantine/core";
import { DateTimePicker } from "@mantine/dates";
import dayjs from "dayjs";

import { usePermissions } from "../auth/usePermissions";
import { useTenantDevices } from "../templates/settingHooks";
import { useLogSearch, type LogSearchIn, type LogSearchOut } from "../logs/logHooks";

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
  const { isOperator } = usePermissions();
  const devices = useTenantDevices();
  const search = useLogSearch();
  const [query, setQuery] = useState("");
  const [deviceId, setDeviceId] = useState<string | null>(null);
  const [frm, setFrm] = useState<string | null>(mantineDaysAgo(1));
  const [to, setTo] = useState<string | null>(dayjs().format(MANTINE_FMT));
  const [hits, setHits] = useState<LogSearchOut["hits"]>([]);
  const [cursor, setCursor] = useState<LogSearchOut["next_cursor"]>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [searched, setSearched] = useState(false);
  const [raw, setRaw] = useState<Record<string, unknown> | null>(null);

  if (!isOperator) {
    return <Alert color="red" data-testid="logs-forbidden">Operators and tenant admins only.</Alert>;
  }

  const deviceName = (id: string) =>
    (devices.data ?? []).find((d) => d.id === id)?.name ?? id;

  async function fetchPage(c: LogSearchOut["next_cursor"]) {
    if (!frm || !to) return;
    try {
      const res = await search.mutateAsync({
        query, device_id: deviceId, frm: toIso(frm), to: toIso(to), size: 100,
        ...(c ? { cursor: c } : {}),
      } as LogSearchIn);
      setHits((prev) => (c ? [...prev, ...res.hits] : res.hits));
      setCursor(res.next_cursor ?? null);
      setTotal(res.total);
      setSearched(true);
    } catch {
      // search.isError drives the error Alert below; swallow to avoid an unhandled rejection
    }
  }
  const run = () => {
    setHits([]);
    setCursor(null);
    setTotal(null);
    fetchPage(null);
  };
  const loadMore = () => fetchPage(cursor);

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
            {total !== null && (
              <Text size="sm" c="dimmed" data-testid="logs-count">
                showing {hits.length} of {total}
              </Text>
            )}
          </Group>
        </Stack>
      </Card>

      {search.isError && <Alert color="red">Log search failed.</Alert>}

      {searched && (
        <Stack>
          <Table highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Time</Table.Th><Table.Th>Device</Table.Th>
                <Table.Th>Program</Table.Th><Table.Th>Message</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {hits.map((h) => (
                <Table.Tr key={h.id} style={{ cursor: "pointer" }} onClick={() => setRaw(h.source)} data-testid={`logrow-${h.id}`}>
                  <Table.Td>{h.timestamp}</Table.Td>
                  <Table.Td>{deviceName(h.device_id)}</Table.Td>
                  <Table.Td>{h.program}</Table.Td>
                  <Table.Td>{h.message}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          {cursor && (
            <Button variant="default" onClick={loadMore} loading={search.isPending} data-testid="logs-loadmore">
              Load more
            </Button>
          )}
        </Stack>
      )}

      <Modal opened={raw !== null} onClose={() => setRaw(null)} title="Raw document" size="lg">
        <Code block data-testid="logs-raw">{JSON.stringify(raw, null, 2)}</Code>
      </Modal>
    </Stack>
  );
}
