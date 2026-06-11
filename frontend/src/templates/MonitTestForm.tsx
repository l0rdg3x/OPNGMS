import { Button, Checkbox, Group, Select, Stack, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useState } from "react";
import { useT } from "../i18n";
import { AutoFormFields } from "./AutoFormFields";
import { initialPayload } from "./OpnsenseSettingForm";
import { type SettingField, useMonitTestModel, useTenantDevices } from "./settingHooks";

type MonitBody = { payload: Record<string, string> };

export function MonitTestForm(
  { value, onChange }: { value: MonitBody; onChange: (v: MonitBody) => void },
) {
  const t = useT();
  const { data: devices } = useTenantDevices();
  const [deviceId, setDeviceId] = useState<string>("");
  const [fields, setFields] = useState<SettingField[]>([]);
  const [loaded, setLoaded] = useState(false);
  const testModel = useMonitTestModel(deviceId);

  const deviceData = (devices ?? []).map((d) => ({ value: d.id, label: d.name }));

  function setField(path: string, v: string) {
    onChange({ payload: { ...value.payload, [path]: v } });
  }

  async function loadFields() {
    try {
      const res = await testModel.mutateAsync();
      const defaults = initialPayload(res.fields);
      const merged = { ...defaults, ...value.payload }; // saved values override device defaults
      setFields(res.fields);
      setLoaded(true);
      onChange({ payload: merged });
    } catch {
      setFields([]);
      setLoaded(false);
      notifications.show({ color: "red", message: t.templates.monit.loadFailed });
    }
  }

  return (
    <Stack>
      {deviceData.length === 0
        ? <Text size="sm" c="dimmed" data-testid="monit-no-device">{t.templates.monit.noDevice}</Text>
        : (
          <>
            <Select
              label={t.templates.monit.referenceDevice}
              data={deviceData}
              data-testid="monit-device"
              value={deviceId || null}
              onChange={(id) => setDeviceId(id ?? "")}
            />
            <Group>
              <Button
                data-testid="monit-load"
                onClick={loadFields}
                loading={testModel.isPending}
                disabled={!deviceId}
              >
                {t.templates.monit.load}
              </Button>
            </Group>
          </>
        )}

      {!loaded
        ? <Text size="sm" c="dimmed" data-testid="monit-load-hint">{t.templates.monit.loadHint}</Text>
        : (
          <Stack data-testid="monit-fields">
            <AutoFormFields fields={fields} payload={value.payload} onField={setField} testidPrefix="monit" />
          </Stack>
        )}

      <Checkbox
        data-testid="monit-attach-system"
        label={t.templates.monit.attachSystem}
        description={t.templates.monit.attachSystemNote}
        checked={value.payload.attach_to_system === "1"}
        onChange={(e) =>
          onChange({
            payload: { ...value.payload, attach_to_system: e.currentTarget.checked ? "1" : "0" },
          })}
      />

      <Text size="xs" c="dimmed" data-testid="monit-note">{t.templates.monit.note}</Text>
    </Stack>
  );
}
