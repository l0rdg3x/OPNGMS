import { Badge, Button, Card, Group, Modal, Stack, Table, Text, TextInput, Title } from "@mantine/core";
import { DateTimePicker } from "@mantine/dates";
import { notifications } from "@mantine/notifications";
import { useState } from "react";
import { useT } from "../i18n";
import {
  type FirmwareActionIn,
  useCreateFirmwareAction,
  useFirmwareActions,
  useFirmwareCheck,
} from "./hooks";

// FirmwareActionIn["kind"] is plain `string` in the generated schema, so we
// define the four valid values locally to keep tsc happy.
type Kind = "firmware_update" | "firmware_upgrade" | "plugin_install" | "plugin_remove";
type Pending = { kind: Kind; target: string; confirm: string };

export function FirmwareActions({ deviceId }: { deviceId: string }) {
  const t = useT();
  const check = useFirmwareCheck(deviceId);
  const create = useCreateFirmwareAction(deviceId);
  const actions = useFirmwareActions(deviceId);
  const [pluginName, setPluginName] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  const [when, setWhen] = useState<string | null>(null);

  const result = check.data;
  const hasUpdates = !!result && (result.status.toLowerCase() === "ok" || result.updates > 0);

  function open(kind: Kind, target: string, confirm: string) {
    setWhen(null);
    setPending({ kind, target, confirm });
  }

  async function fire(scheduled: boolean) {
    if (!pending) return;
    const body: FirmwareActionIn = {
      kind: pending.kind,
      target: pending.target,
      scheduled_at: scheduled && when ? new Date(when.replace(" ", "T")).toISOString() : null,
    };
    try {
      await create.mutateAsync(body);
      notifications.show({ message: t.firmware.actionQueued });
    } catch {
      notifications.show({ color: "red", message: t.firmware.actionFailed });
    } finally {
      setPending(null);
    }
  }

  return (
    <Stack mt="md">
      <Card withBorder>
        <Group justify="space-between" mb="xs">
          <Title order={5}>{t.firmware.title}</Title>
          <Button size="xs" onClick={() => check.mutate()} loading={check.isPending} data-testid="btn-fw-check">
            {t.firmware.check}
          </Button>
        </Group>
        {result && (
          <Stack gap={4}>
            <Text data-testid="fw-check-result">
              {hasUpdates ? t.firmware.updatesAvailable : t.firmware.upToDate}
              {result.updates > 0 ? ` (${result.updates})` : ""}
            </Text>
            <Text size="sm" c="dimmed">
              {t.firmware.downloadSize}: {result.download_size || t.common.none} · {t.firmware.rebootNeeded}:{" "}
              {result.needs_reboot ? "yes" : "no"}
            </Text>
            <Group mt="xs">
              <Button
                size="xs"
                disabled={!hasUpdates}
                onClick={() => open("firmware_update", "", t.firmware.updateConfirm)}
                data-testid="btn-fw-update"
              >
                {t.firmware.update}
              </Button>
              {result.new_major && (
                <Button
                  size="xs"
                  color="orange"
                  onClick={() => open("firmware_upgrade", "", t.firmware.upgradeConfirm)}
                  data-testid="btn-fw-upgrade"
                >
                  {t.firmware.upgrade}
                </Button>
              )}
            </Group>
          </Stack>
        )}
      </Card>

      <Card withBorder>
        <Title order={5} mb="xs">{t.firmware.plugins}</Title>
        <Group align="flex-end">
          <TextInput
            label={t.firmware.pluginName}
            value={pluginName}
            onChange={(e) => setPluginName(e.currentTarget.value)}
            data-testid="input-plugin-name"
          />
          <Button
            size="sm"
            disabled={!pluginName.trim()}
            onClick={() => open("plugin_install", pluginName.trim(), t.firmware.installConfirm)}
            data-testid="btn-plugin-install"
          >
            {t.firmware.install}
          </Button>
          <Button
            size="sm"
            variant="light"
            color="red"
            disabled={!pluginName.trim()}
            onClick={() => open("plugin_remove", pluginName.trim(), t.firmware.removeConfirm)}
            data-testid="btn-plugin-remove"
          >
            {t.firmware.remove}
          </Button>
        </Group>
      </Card>

      <Card withBorder>
        <Title order={5} mb="xs">{t.firmware.recentActions}</Title>
        {actions.data && actions.data.length > 0 ? (
          <Table data-testid="fw-actions-list">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t.firmware.kind}</Table.Th>
                <Table.Th>{t.firmware.status}</Table.Th>
                <Table.Th>{t.firmware.when}</Table.Th>
                <Table.Th>{t.firmware.result}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {actions.data.map((a) => {
                const r = a.result as { version?: string; error?: string };
                return (
                  <Table.Tr key={a.id}>
                    <Table.Td>{a.kind}{a.target ? `: ${a.target}` : ""}</Table.Td>
                    <Table.Td><Badge variant="light">{a.status}</Badge></Table.Td>
                    <Table.Td>{a.scheduled_at ?? a.created_at}</Table.Td>
                    <Table.Td>{r.version ?? r.error ?? ""}</Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
        ) : (
          <Text c="dimmed" size="sm">{t.firmware.noActions}</Text>
        )}
      </Card>

      <Modal
        opened={!!pending}
        onClose={() => setPending(null)}
        title={t.confirm.title}
        data-testid="fw-confirm-modal"
        transitionProps={{ duration: 0 }}
      >
        <Stack>
          <Text>{pending?.confirm}</Text>
          <DateTimePicker
            label={t.firmware.scheduleAt}
            value={when}
            onChange={setWhen}
            minDate={new Date()}
            clearable
            data-testid="fw-schedule-picker"
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setPending(null)} data-testid="btn-fw-cancel">
              {t.confirm.cancel}
            </Button>
            <Button
              variant="light"
              onClick={() => fire(false)}
              loading={create.isPending}
              data-testid="btn-fw-confirm-now"
            >
              {t.firmware.runNow}
            </Button>
            <Button
              onClick={() => fire(true)}
              disabled={!when}
              loading={create.isPending}
              data-testid="btn-fw-confirm-schedule"
            >
              {t.firmware.schedule}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
