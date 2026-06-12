import { useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Card,
  Code,
  Group,
  Loader,
  Modal,
  Popover,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { DateTimePicker } from "@mantine/dates";
import { notifications } from "@mantine/notifications";
import { useT } from "../i18n";
import { useTenant } from "../tenant/useTenant";
import type { ConfigChange } from "./changeTypes";
import {
  useCancelChange,
  useConfigChanges,
  usePreviewChange,
  useRevertChange,
  useScheduleChange,
} from "./changeHooks";
import { ProposeAliasModal } from "./ProposeAliasModal";

// Pipeline status -> Mantine badge color.
const STATUS_COLOR: Record<string, string> = {
  draft: "gray",
  scheduled: "blue",
  applying: "yellow",
  applied: "green",
  conflict: "orange",
  failed: "red",
  cancelled: "gray",
};

// Statuses that allow editing actions.
const ACTIONABLE = new Set(["draft", "scheduled"]);

// Per-row action buttons for draft/scheduled changes.
function ChangeRowActions({
  deviceId,
  c,
  onPreview,
}: {
  deviceId: string;
  c: ConfigChange;
  onPreview: (id: string) => void;
}) {
  const t = useT();
  const schedule = useScheduleChange(deviceId);
  const cancel = useCancelChange(deviceId);
  const [scheduleDate, setScheduleDate] = useState<string | null>(null);
  const [scheduleOpen, setScheduleOpen] = useState(false);

  async function handleApplyNow() {
    try {
      await schedule.mutateAsync({ id: c.id, scheduled_at: null });
    } catch {
      notifications.show({ color: "red", message: t.errors.configChangeAction });
    }
  }

  async function handleSchedule() {
    const iso = scheduleDate ? new Date(scheduleDate.replace(" ", "T")).toISOString() : null;
    try {
      await schedule.mutateAsync({ id: c.id, scheduled_at: iso });
      setScheduleOpen(false);
      setScheduleDate(null);
    } catch {
      notifications.show({ color: "red", message: t.errors.configChangeAction });
    }
  }

  async function handleCancel() {
    try {
      await cancel.mutateAsync(c.id);
    } catch {
      notifications.show({ color: "red", message: t.errors.configChangeAction });
    }
  }

  return (
    <Group gap="xs" wrap="nowrap">
      <Button size="xs" variant="light" onClick={() => onPreview(c.id)}>
        {t.config.changes.preview}
      </Button>

      <Popover opened={scheduleOpen} onChange={setScheduleOpen} withinPortal>
        <Popover.Target>
          <Button
            size="xs"
            variant="light"
            onClick={() => setScheduleOpen((v) => !v)}
          >
            {t.config.changes.schedule}
          </Button>
        </Popover.Target>
        <Popover.Dropdown>
          <Stack gap="xs">
            <Button
              size="xs"
              variant="subtle"
              onClick={handleApplyNow}
              loading={schedule.isPending}
            >
              {t.config.changes.applyNow}
            </Button>
            <DateTimePicker
              label={t.config.changes.pickTime}
              value={scheduleDate}
              onChange={setScheduleDate}
              minDate={new Date()}
              clearable
            />
            <Button
              size="xs"
              onClick={handleSchedule}
              loading={schedule.isPending}
              disabled={!scheduleDate}
            >
              {t.config.changes.schedule}
            </Button>
          </Stack>
        </Popover.Dropdown>
      </Popover>

      <Button
        size="xs"
        color="red"
        variant="light"
        onClick={handleCancel}
        loading={cancel.isPending}
      >
        {t.config.changes.cancel}
      </Button>
    </Group>
  );
}

// Revert action for an already-applied, revertible change. Mirrors the
// direct-mutate pattern used by the other row actions (no confirm modal).
function RevertButton({ deviceId, c }: { deviceId: string; c: ConfigChange }) {
  const t = useT();
  const revert = useRevertChange(deviceId);

  async function handleRevert() {
    try {
      await revert.mutateAsync(c.id);
    } catch {
      notifications.show({ color: "red", message: t.errors.configChangeAction });
    }
  }

  return (
    <Button
      size="xs"
      color="orange"
      variant="light"
      data-testid={`revert-${c.id}`}
      onClick={handleRevert}
      loading={revert.isPending}
    >
      {t.config.changes.revert}
    </Button>
  );
}

// Preview modal: displays the server-returned preview dict read-only.
function PreviewModal({
  deviceId,
  previewId,
  onClose,
}: {
  deviceId: string;
  previewId: string | null;
  onClose: () => void;
}) {
  const t = useT();
  const preview = usePreviewChange(deviceId, previewId);

  return (
    <Modal opened={!!previewId} onClose={onClose} title={t.config.changes.preview}>
      {preview.isLoading && <Loader size="sm" />}
      {preview.isError && (
        <Text c="red">{t.errors.configChangeAction}</Text>
      )}
      {preview.data != null && (
        <Code block>{JSON.stringify(preview.data, null, 2)}</Code>
      )}
    </Modal>
  );
}

export function ChangesPanel({ deviceId }: { deviceId: string }) {
  const t = useT();
  const { activeId, tenants } = useTenant();
  const role = tenants.find((x) => x.id === activeId)?.role ?? null;
  const canEdit = role === "tenant_admin" || role === "operator";
  const q = useConfigChanges(deviceId);
  const [proposeOpen, setProposeOpen] = useState(false);
  const [previewId, setPreviewId] = useState<string | null>(null);

  return (
    <Card withBorder>
      <Group justify="space-between" mb="xs">
        <Title order={5}>{t.config.changes.title}</Title>
        {canEdit && (
          <Button size="xs" onClick={() => setProposeOpen(true)}>
            {t.config.changes.propose}
          </Button>
        )}
      </Group>
      {q.isLoading && <Loader size="sm" />}
      {q.isError && <Alert color="red">{t.errors.configChangesLoad}</Alert>}
      {q.data && q.data.length === 0 && (
        <Text c="dimmed">{t.config.changes.none}</Text>
      )}
      {q.data && q.data.length > 0 && (
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t.config.changes.colKind}</Table.Th>
              <Table.Th>{t.config.changes.colOperation}</Table.Th>
              <Table.Th>{t.config.changes.colTarget}</Table.Th>
              <Table.Th>{t.config.changes.colStatus}</Table.Th>
              <Table.Th>{t.config.changes.colScheduled}</Table.Th>
              {canEdit && <Table.Th />}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {q.data.map((c) => (
              <Table.Tr key={c.id}>
                <Table.Td>{c.kind}</Table.Td>
                <Table.Td>{c.operation}</Table.Td>
                <Table.Td>{c.target}</Table.Td>
                <Table.Td>
                  <Badge color={STATUS_COLOR[c.status] ?? "gray"}>
                    {c.status}
                  </Badge>
                  {c.reverts_change_id && (
                    <Text size="xs" c="dimmed">
                      reverts #{c.reverts_change_id.slice(0, 7)}
                    </Text>
                  )}
                </Table.Td>
                <Table.Td>{c.scheduled_at ?? "—"}</Table.Td>
                {canEdit && (
                  <Table.Td>
                    {ACTIONABLE.has(c.status) && (
                      <ChangeRowActions
                        deviceId={deviceId}
                        c={c}
                        onPreview={setPreviewId}
                      />
                    )}
                    {c.revertible && <RevertButton deviceId={deviceId} c={c} />}
                  </Table.Td>
                )}
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      <ProposeAliasModal
        deviceId={deviceId}
        opened={proposeOpen}
        onClose={() => setProposeOpen(false)}
      />

      <PreviewModal
        deviceId={deviceId}
        previewId={previewId}
        onClose={() => setPreviewId(null)}
      />
    </Card>
  );
}
