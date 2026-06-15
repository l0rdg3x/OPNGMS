import { Alert, Badge, Button, Group, Loader, Stack, Table, Text } from "@mantine/core";

import { useT, type Dict } from "../i18n";
import { type EventOut, useConfigAuditEvents } from "./configAuditHooks";

/** True for a DIRECT on-box change (a drift cause): a console/script (`system`) or WebGUI (`gui`) write. */
function isDirect(action: string): boolean {
  return action === "gui" || action === "system";
}

/** Localized label for a change channel, falling back to the raw value for unknown channels. */
function channelLabel(action: string, tr: Dict["configAudit"]): string {
  const labels: Record<string, string> = tr.channels;
  return labels[action] ?? action;
}

/** Pull a string field out of the event's free-form `attributes` map (empty string if absent). */
function attr(event: EventOut, key: string): string {
  const value = event.attributes?.[key];
  return typeof value === "string" ? value : "";
}

/**
 * Per-device config-change audit timeline: a keyset-paginated table of the device's
 * `source="config_audit"` events (who/what/when changed the box config, channel-attributed) read
 * from the existing events API. A "Direct" badge flags on-box (gui/system) drift changes.
 */
export function ConfigAuditTab({ deviceId }: { deviceId: string }) {
  const t = useT();
  const tr = t.configAudit;
  const { data, isLoading, error, fetchNextPage, hasNextPage, isFetchingNextPage } =
    useConfigAuditEvents(deviceId);

  const rows = data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <Stack gap="md" data-testid="config-audit-tab">
      <Text c="dimmed" size="sm">{tr.subtitle}</Text>
      {isLoading && <Loader size="sm" aria-label={tr.loading} />}
      {error && <Alert color="red">{tr.loadError}</Alert>}
      {data && rows.length === 0 && <Text size="sm" c="dimmed">{tr.empty}</Text>}
      {rows.length > 0 && (
        <Table striped withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{tr.time}</Table.Th>
              <Table.Th>{tr.area}</Table.Th>
              <Table.Th>{tr.actor}</Table.Th>
              <Table.Th>{tr.ip}</Table.Th>
              <Table.Th>{tr.channel}</Table.Th>
              <Table.Th>{tr.change}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((e, i) => (
              // Append-only keyset list (pages only append): the flat index is a stable, collision-free
              // key. EventOut exposes no single id; device_id+time make it human-traceable.
              <Table.Tr key={`${e.device_id}-${e.time}-${i}`}>
                <Table.Td>{new Date(e.time).toLocaleString()}</Table.Td>
                <Table.Td>{e.category}</Table.Td>
                <Table.Td>{e.name}</Table.Td>
                <Table.Td>{e.src_ip}</Table.Td>
                <Table.Td>
                  <Group gap="xs" wrap="nowrap">
                    <Text size="sm">{channelLabel(e.action, tr)}</Text>
                    {isDirect(e.action) && <Badge color="yellow">{tr.direct}</Badge>}
                  </Group>
                </Table.Td>
                <Table.Td>
                  <Text size="xs" lineClamp={2}>{attr(e, "change_ref")}</Text>
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
