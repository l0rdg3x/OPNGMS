import { Alert, Badge, Button, Group, Loader, Stack, Table, Text } from "@mantine/core";

import { useT, type Dict } from "../i18n";
import { type EventOut, useReliabilityEvents } from "./reliabilityHooks";

/** Map an event severity to a Mantine badge color: high=red, medium=yellow, low/other=gray. */
function severityColor(severity: string): string {
  if (severity === "high") return "red";
  if (severity === "medium") return "yellow";
  return "gray"; // low + any unknown severity
}

/** Localized label for a severity, falling back to the raw value for unknown severities. */
function severityLabel(severity: string, tr: Dict["reliability"]): string {
  const labels: Record<string, string> = tr.severities;
  return labels[severity] ?? severity;
}

/** Localized label for a category, falling back to the raw value for unknown categories. */
function categoryLabel(category: string, tr: Dict["reliability"]): string {
  const labels: Record<string, string> = tr.categories;
  return labels[category] ?? category;
}

/** Pull a string field out of the event's free-form `attributes` map (empty string if absent). */
function attr(event: EventOut, key: string): string {
  const value = event.attributes?.[key];
  return typeof value === "string" ? value : "";
}

/**
 * Per-device reliability timeline: a keyset-paginated table of the device's `source="service"`
 * events (reboots, service crashes/restarts, disk warnings) read from the existing events API.
 */
export function ReliabilityTab({ deviceId }: { deviceId: string }) {
  const t = useT();
  const tr = t.reliability;
  const { data, isLoading, error, fetchNextPage, hasNextPage, isFetchingNextPage } =
    useReliabilityEvents(deviceId);

  const rows = data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <Stack gap="md" data-testid="reliability-tab">
      <Text c="dimmed" size="sm">{tr.subtitle}</Text>
      {isLoading && <Loader size="sm" aria-label={tr.loading} />}
      {error && <Alert color="red">{tr.loadError}</Alert>}
      {data && rows.length === 0 && <Text size="sm" c="dimmed">{tr.empty}</Text>}
      {rows.length > 0 && (
        <Table striped withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{tr.time}</Table.Th>
              <Table.Th>{tr.category}</Table.Th>
              <Table.Th>{tr.name}</Table.Th>
              <Table.Th>{tr.severity}</Table.Th>
              <Table.Th>{tr.process}</Table.Th>
              <Table.Th>{tr.message}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((e, i) => (
              // Append-only keyset list (pages only append): the flat index is a stable, collision-free
              // key. EventOut exposes no single id; device_id+time make it human-traceable.
              <Table.Tr key={`${e.device_id}-${e.time}-${i}`}>
                <Table.Td>{new Date(e.time).toLocaleString()}</Table.Td>
                <Table.Td>{categoryLabel(e.category, tr)}</Table.Td>
                <Table.Td>{e.name}</Table.Td>
                <Table.Td>
                  <Badge color={severityColor(e.severity)}>{severityLabel(e.severity, tr)}</Badge>
                </Table.Td>
                <Table.Td>{attr(e, "process")}</Table.Td>
                <Table.Td>
                  <Text size="xs" lineClamp={2}>{attr(e, "message")}</Text>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      {hasNextPage && (
        <Group>
          <Button
            variant="default"
            size="xs"
            onClick={() => fetchNextPage()}
            loading={isFetchingNextPage}
          >
            {tr.loadMore}
          </Button>
        </Group>
      )}
    </Stack>
  );
}
