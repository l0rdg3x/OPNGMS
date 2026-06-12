import { useEffect, useRef, useState } from "react";
import {
  Alert, Button, Card, Group, NumberInput, Select, Stack, Switch, Text, Textarea, Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";

import { useTenant } from "../tenant/useTenant";
import {
  useReportSchedules, useSendScheduleNow, useUpsertReportSchedule,
} from "../reports/scheduleHooks";
import type { ScheduleIn } from "../reports/scheduleHooks";

const WEEKDAYS = [
  { value: "0", label: "Monday" }, { value: "1", label: "Tuesday" }, { value: "2", label: "Wednesday" },
  { value: "3", label: "Thursday" }, { value: "4", label: "Friday" }, { value: "5", label: "Saturday" },
  { value: "6", label: "Sunday" },
];

export function ReportSchedulePage() {
  const { activeId, tenants } = useTenant();
  const role = tenants.find((tn) => tn.id === activeId)?.role ?? null;
  const query = useReportSchedules();
  const upsert = useUpsertReportSchedule();
  const sendNow = useSendScheduleNow();
  const loaded = useRef(false);

  const [enabled, setEnabled] = useState(true);
  const [frequency, setFrequency] = useState("weekly");
  const [weekday, setWeekday] = useState<string | null>("0");
  const [hour, setHour] = useState(4);
  const [recipients, setRecipients] = useState("");

  const fleet = query.data?.find((s) => s.device_id === null);

  useEffect(() => {
    if (query.data && !loaded.current) {
      if (fleet) {
        setEnabled(fleet.enabled); setFrequency(fleet.frequency);
        setWeekday(fleet.weekday === null ? null : String(fleet.weekday));
        setHour(fleet.hour); setRecipients((fleet.recipients ?? []).join("\n"));
      }
      loaded.current = true;
    }
  }, [query.data]);  // eslint-disable-line react-hooks/exhaustive-deps

  if (role !== "tenant_admin") {
    return <Alert color="red" data-testid="schedule-forbidden">Tenant admins only.</Alert>;
  }

  async function save() {
    const body: ScheduleIn = {
      device_id: null, enabled, frequency,
      weekday: frequency === "weekly" ? Number(weekday ?? 0) : null,
      hour, recipients: recipients.split(/[\n,]/).map((r) => r.trim()).filter(Boolean),
    };
    try {
      await upsert.mutateAsync(body);
      notifications.show({ message: "Schedule saved" });
    } catch {
      notifications.show({ color: "red", message: "Failed to save schedule" });
    }
  }

  async function triggerNow() {
    if (!fleet) return;
    try {
      await sendNow.mutateAsync(fleet.id);
      notifications.show({ message: "Report send queued" });
    } catch {
      notifications.show({ color: "red", message: "Failed to queue send" });
    }
  }

  return (
    <Stack maw={560}>
      <Title order={3}>Report delivery schedule</Title>
      <Text size="sm" c="dimmed">Email the fleet report to recipients on a cadence.</Text>
      <Card withBorder padding="lg" radius="md">
        <Stack>
          <Switch label="Enabled" checked={enabled} onChange={(e) => setEnabled(e.currentTarget.checked)} data-testid="fleet-enabled" />
          <Select label="Frequency" data={[
            { value: "weekly", label: "Weekly" }, { value: "monthly", label: "Monthly (1st)" },
            { value: "on_demand", label: "On demand only" },
          ]} value={frequency} onChange={(v) => setFrequency(v ?? "weekly")} data-testid="fleet-frequency" />
          {frequency === "weekly" && (
            <Select label="Day of week" data={WEEKDAYS} value={weekday} onChange={setWeekday} data-testid="fleet-weekday" />
          )}
          {frequency !== "on_demand" && (
            <NumberInput label="Hour (UTC)" min={0} max={23} value={hour} onChange={(v) => setHour(Number(v))} data-testid="fleet-hour" />
          )}
          <Textarea label="Recipients (one per line)" value={recipients} onChange={(e) => setRecipients(e.currentTarget.value)} data-testid="fleet-recipients" minRows={3} rows={3} />
          <Group>
            <Button onClick={save} loading={upsert.isPending} data-testid="fleet-save">Save</Button>
            {fleet && <Button variant="light" onClick={triggerNow} loading={sendNow.isPending} data-testid="fleet-send-now">Send now</Button>}
          </Group>
        </Stack>
      </Card>
    </Stack>
  );
}
