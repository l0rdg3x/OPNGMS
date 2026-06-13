import { Badge, Button, Card, Group, Modal, Stack, Table, Text, TextInput, Title } from "@mantine/core";
import { useMemo, useState } from "react";
import { usePermissions } from "../auth/usePermissions";
import { useCreateFirmwareAction } from "../firmware/hooks";
import { useT } from "../i18n";
import { type PluginInfo, useDevicePlugins } from "./pluginsHooks";

/** Strip the `os-` package prefix for a friendlier display title (keep the full name as the id). */
function title(name: string): string {
  return name.startsWith("os-") ? name.slice(3) : name;
}

export function PluginsTab({ deviceId }: { deviceId: string }) {
  const t = useT();
  const { isOperator: canWrite } = usePermissions();
  const plugins = useDevicePlugins(deviceId);
  const create = useCreateFirmwareAction(deviceId);
  const [search, setSearch] = useState("");
  const [confirm, setConfirm] = useState<{ kind: "plugin_install" | "plugin_remove"; name: string } | null>(null);

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    const list = (plugins.data ?? []).filter((p) => !q || p.name.toLowerCase().includes(q));
    // Installed first, then alphabetical.
    return [...list].sort((a, b) =>
      a.installed === b.installed ? a.name.localeCompare(b.name) : a.installed ? -1 : 1);
  }, [plugins.data, search]);

  async function run() {
    if (!confirm) return;
    await create.mutateAsync({ kind: confirm.kind, target: confirm.name });
    setConfirm(null);
    await plugins.refetch();
  }

  if (plugins.isError) {
    return <Text c="red" size="sm">{t.plugins.loadFailed}</Text>;
  }

  return (
    <Card withBorder>
      <Stack>
        <Group justify="space-between">
          <Title order={5}>{t.plugins.title}</Title>
          <TextInput
            placeholder={t.plugins.search}
            value={search}
            onChange={(e) => setSearch(e.currentTarget.value)}
            data-testid="plugins-search"
          />
        </Group>
        <Text c="dimmed" size="xs">{t.plugins.subtitle}</Text>
        <Table data-testid="plugins-list">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t.plugins.name}</Table.Th>
              <Table.Th>{t.plugins.status}</Table.Th>
              <Table.Th>{t.plugins.version}</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((p: PluginInfo) => (
              <Table.Tr key={p.name}>
                <Table.Td>
                  <Text fw={500}>{title(p.name)}</Text>
                  <Text c="dimmed" size="xs">{p.name}</Text>
                </Table.Td>
                <Table.Td>
                  {p.installed
                    ? <Badge color="green" variant="light">{t.plugins.installed}</Badge>
                    : <Badge color="gray" variant="light">{t.plugins.notInstalled}</Badge>}
                  {p.locked && <Badge color="yellow" variant="light" ml="xs">{t.plugins.locked}</Badge>}
                </Table.Td>
                <Table.Td>{p.version || "—"}</Table.Td>
                <Table.Td>
                  {canWrite && !p.locked && (
                    p.installed
                      ? <Button size="xs" variant="light" color="red"
                          data-testid={`plugin-remove-${p.name}`}
                          onClick={() => setConfirm({ kind: "plugin_remove", name: p.name })}>
                          {t.plugins.remove}
                        </Button>
                      : <Button size="xs"
                          data-testid={`plugin-install-${p.name}`}
                          onClick={() => setConfirm({ kind: "plugin_install", name: p.name })}>
                          {t.plugins.install}
                        </Button>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        {rows.length === 0 && <Text c="dimmed" size="sm">{t.plugins.empty}</Text>}
      </Stack>

      <Modal opened={confirm !== null} onClose={() => setConfirm(null)}
             title={confirm?.kind === "plugin_install" ? t.plugins.installConfirm : t.plugins.removeConfirm}>
        <Text size="sm" mb="md">{confirm ? title(confirm.name) : ""}</Text>
        <Group justify="flex-end">
          <Button variant="default" onClick={() => setConfirm(null)}>{t.common.cancel}</Button>
          <Button color={confirm?.kind === "plugin_remove" ? "red" : undefined}
                  loading={create.isPending} onClick={run} data-testid="plugin-confirm">
            {confirm?.kind === "plugin_install" ? t.plugins.install : t.plugins.remove}
          </Button>
        </Group>
      </Modal>
    </Card>
  );
}
