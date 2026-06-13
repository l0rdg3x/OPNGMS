import { useState } from "react";
import {
  Accordion, Alert, Badge, Button, Card, Group, NumberInput, Select, Stack, Switch, Text, Textarea, Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";

import { usePermissions } from "../auth/usePermissions";
import { useT } from "../i18n";
import {
  type ScheduleIn, type ScheduleOut, useReportSchedules, useSendScheduleNow, useUpsertReportSchedule,
} from "../reports/scheduleHooks";
import {
  buildSectionsMap, REPORT_SECTION_KEYS, type ReportSectionKey, seedSectionState, sectionLabel,
} from "../reports/sections";
import { useTenantDevices } from "../templates/settingHooks";

const WEEKDAYS = [
  { value: "0", label: "Monday" }, { value: "1", label: "Tuesday" }, { value: "2", label: "Wednesday" },
  { value: "3", label: "Thursday" }, { value: "4", label: "Friday" }, { value: "5", label: "Saturday" },
  { value: "6", label: "Sunday" },
];

const WEEKDAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function ScheduleStatus({ existing }: { existing: ScheduleOut | undefined }) {
  if (!existing) {
    return <Text size="sm" c="dimmed">Not scheduled</Text>;
  }
  if (!existing.enabled) {
    return <Badge color="gray" variant="light">Disabled</Badge>;
  }
  const hh = String(existing.hour).padStart(2, "0");
  const recipients = existing.recipients?.length ?? 0;
  const recipientSuffix = recipients > 0 ? ` · ${recipients} recipient${recipients === 1 ? "" : "s"}` : "";
  let summary: string;
  if (existing.frequency === "weekly") {
    const day = WEEKDAY_SHORT[existing.weekday ?? 0] ?? WEEKDAY_SHORT[0];
    summary = `Weekly · ${day} ${hh}:00`;
  } else if (existing.frequency === "monthly") {
    summary = `Monthly · ${hh}:00`;
  } else {
    summary = "On demand";
  }
  return <Badge color="teal" variant="light">{`${summary}${recipientSuffix}`}</Badge>;
}

function ScheduleEditor({ prefix, deviceId, existing }: {
  prefix: string;
  deviceId: string | null;
  existing: ScheduleOut | undefined;
}) {
  const t = useT();
  const upsert = useUpsertReportSchedule();
  const sendNow = useSendScheduleNow();
  const [enabled, setEnabled] = useState(existing?.enabled ?? (deviceId === null));
  const [frequency, setFrequency] = useState<string>(existing?.frequency ?? "weekly");
  const [weekday, setWeekday] = useState<string | null>(
    existing?.weekday != null ? String(existing.weekday) : "0");
  const [hour, setHour] = useState<number>(existing?.hour ?? 4);
  const [recipients, setRecipients] = useState((existing?.recipients ?? []).join("\n"));
  // Per-device section override: on ⇒ send an explicit map; off ⇒ send null (inherit the tenant default).
  const [customizeSections, setCustomizeSections] = useState(existing?.sections != null);
  const [sections, setSections] = useState<Record<ReportSectionKey, boolean>>(() =>
    seedSectionState(existing?.sections));

  async function save() {
    const body: ScheduleIn = {
      device_id: deviceId, enabled, frequency,
      weekday: frequency === "weekly" ? Number(weekday ?? 0) : null,
      hour, recipients: recipients.split(/[\n,]/).map((r) => r.trim()).filter(Boolean),
      sections: deviceId !== null && customizeSections ? buildSectionsMap(sections) : null,
    };
    try {
      await upsert.mutateAsync(body);
      notifications.show({ message: "Schedule saved" });
    } catch {
      notifications.show({ color: "red", message: "Failed to save schedule" });
    }
  }

  async function triggerNow() {
    if (!existing) return;
    try {
      await sendNow.mutateAsync(existing.id);
      notifications.show({ message: "Report send queued" });
    } catch {
      notifications.show({ color: "red", message: "Failed to queue send" });
    }
  }

  return (
    <Stack>
      <Switch label="Enabled" checked={enabled} onChange={(e) => setEnabled(e.currentTarget.checked)} data-testid={`${prefix}-enabled`} />
      <Select label="Frequency" data={[
        { value: "weekly", label: "Weekly" }, { value: "monthly", label: "Monthly (1st)" },
        { value: "on_demand", label: "On demand only" },
      ]} value={frequency} onChange={(v) => setFrequency(v ?? "weekly")} data-testid={`${prefix}-frequency`} />
      {frequency === "weekly" && (
        <Select label="Day of week" data={WEEKDAYS} value={weekday} onChange={setWeekday} data-testid={`${prefix}-weekday`} />
      )}
      {frequency !== "on_demand" && (
        <NumberInput label="Hour (UTC)" min={0} max={23} value={hour} onChange={(v) => setHour(Number(v))} data-testid={`${prefix}-hour`} />
      )}
      <Textarea label="Recipients (one per line)" value={recipients} onChange={(e) => setRecipients(e.currentTarget.value)} data-testid={`${prefix}-recipients`} minRows={3} rows={3} />
      {deviceId !== null && (
        <>
          <Switch
            label={t.reports.sections.customizeForDevice}
            checked={customizeSections}
            onChange={(e) => setCustomizeSections(e.currentTarget.checked)}
            data-testid={`${prefix}-customize-sections`}
          />
          {customizeSections && (
            <Stack gap="xs">
              <Text size="sm" c="dimmed">{t.reports.sections.description}</Text>
              {REPORT_SECTION_KEYS.map((key) => (
                <Switch
                  key={key}
                  label={sectionLabel(t, key)}
                  checked={sections[key]}
                  onChange={(e) => setSections((prev) => ({ ...prev, [key]: e.currentTarget.checked }))}
                  data-testid={`${prefix}-section-toggle-${key}`}
                />
              ))}
            </Stack>
          )}
        </>
      )}
      <Group>
        <Button onClick={save} loading={upsert.isPending} data-testid={`${prefix}-save`}>Save</Button>
        {existing && <Button variant="light" onClick={triggerNow} loading={sendNow.isPending} data-testid={`${prefix}-send-now`}>Send now</Button>}
      </Group>
    </Stack>
  );
}

export function ReportSchedulePage() {
  const { isTenantAdmin } = usePermissions();
  const schedules = useReportSchedules();
  const devices = useTenantDevices();

  if (!isTenantAdmin) {
    return <Alert color="red" data-testid="schedule-forbidden">Tenant admins only.</Alert>;
  }

  const fleet = schedules.data?.find((s) => s.device_id === null);
  const deviceList = devices.data ?? [];

  return (
    <Stack maw={640}>
      <Title order={3}>Report delivery schedule</Title>
      <Text size="sm" c="dimmed">Email reports to recipients on a cadence — for the whole fleet and per device.</Text>

      <Card withBorder padding="lg" radius="md">
        <Stack>
          <Title order={5}>Fleet report</Title>
          {schedules.isSuccess && (
            <ScheduleEditor key={fleet?.id ?? "fleet-new"} prefix="fleet" deviceId={null} existing={fleet} />
          )}
        </Stack>
      </Card>

      <Title order={5}>Per-device reports</Title>
      {deviceList.length === 0 ? (
        <Text size="sm" c="dimmed">No devices in this tenant.</Text>
      ) : (
        <Accordion variant="separated" multiple>
          {deviceList.map((d) => {
            const existing = schedules.data?.find((s) => s.device_id === d.id);
            return (
              <Accordion.Item value={d.id} key={d.id}>
                <Accordion.Control data-testid={`device-schedule-row-${d.id}`}>
                  <Group justify="space-between" wrap="nowrap" pr="sm">
                    <Text fw={600}>{d.name}</Text>
                    <ScheduleStatus existing={existing} />
                  </Group>
                </Accordion.Control>
                <Accordion.Panel>
                  {schedules.isSuccess && (
                    <ScheduleEditor key={existing?.id ?? `device-${d.id}-new`} prefix={`device-${d.id}`} deviceId={d.id} existing={existing} />
                  )}
                </Accordion.Panel>
              </Accordion.Item>
            );
          })}
        </Accordion>
      )}
    </Stack>
  );
}
