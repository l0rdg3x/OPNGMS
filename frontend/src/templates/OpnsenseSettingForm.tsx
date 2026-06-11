import { Button, Group, Select, Stack, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useState } from "react";
import { useT } from "../i18n";
import { type SettingField, useIntrospectSetting, useSettingEndpoints, useTenantDevices } from "./settingHooks";
import { AutoFormFields } from "./AutoFormFields";

type SettingBody = { endpoint_key: string; payload: Record<string, string> };

// eslint-disable-next-line react-refresh/only-export-components
export function initialPayload(fields: SettingField[]): Record<string, string> {
  const payload: Record<string, string> = {};
  for (const f of fields) {
    payload[f.path] = Array.isArray(f.value) ? f.value.join(",") : String(f.value);
  }
  return payload;
}

export function OpnsenseSettingForm(
  { value, onChange }: { value: SettingBody; onChange: (v: SettingBody) => void },
) {
  const t = useT();
  const { data: endpoints } = useSettingEndpoints();
  const { data: devices } = useTenantDevices();
  const [deviceId, setDeviceId] = useState<string>("");
  const [fields, setFields] = useState<SettingField[]>([]);
  const [loaded, setLoaded] = useState(false);
  const introspect = useIntrospectSetting(deviceId);

  const endpointData = (endpoints ?? []).map((e) => ({ value: e.key, label: e.label }));
  const deviceData = (devices ?? []).map((d) => ({ value: d.id, label: d.name }));

  function setField(path: string, v: string) {
    onChange({ endpoint_key: value.endpoint_key, payload: { ...value.payload, [path]: v } });
  }

  async function loadFields() {
    try {
      const res = await introspect.mutateAsync(value.endpoint_key);
      const defaults = initialPayload(res.fields);
      const merged = { ...defaults, ...value.payload };   // saved values override device defaults
      setFields(res.fields);
      setLoaded(true);
      onChange({ endpoint_key: value.endpoint_key, payload: merged });
    } catch {
      setFields([]);
      setLoaded(false);
      notifications.show({ color: "red", message: t.templates.setting.loadFailed });
    }
  }

  return (
    <Stack>
      <Select
        label={t.templates.setting.endpoint}
        data={endpointData}
        data-testid="setting-endpoint"
        value={value.endpoint_key || null}
        onChange={(key) => {
          setFields([]);
          setLoaded(false);
          onChange({ endpoint_key: key ?? "", payload: {} });
        }}
      />
      {deviceData.length === 0
        ? <Text size="sm" c="dimmed" data-testid="setting-no-device">{t.templates.setting.noDevice}</Text>
        : (
          <>
            <Select
              label={t.templates.setting.referenceDevice}
              data={deviceData}
              data-testid="setting-device"
              value={deviceId || null}
              onChange={(id) => setDeviceId(id ?? "")}
            />
            <Group>
              <Button
                data-testid="setting-load"
                onClick={loadFields}
                loading={introspect.isPending}
                disabled={!value.endpoint_key || !deviceId}
              >
                {t.templates.setting.load}
              </Button>
            </Group>
          </>
        )}

      {!loaded
        ? <Text size="sm" c="dimmed" data-testid="setting-load-hint">{t.templates.setting.loadHint}</Text>
        : fields.length === 0
          ? <Text size="sm" c="dimmed" data-testid="setting-no-fields">{t.templates.setting.noFields}</Text>
          : (
          <Stack data-testid="setting-fields">
            <AutoFormFields fields={fields} payload={value.payload} onField={setField} />
          </Stack>
        )}

      <Text size="xs" c="dimmed" data-testid="setting-hardware-note">{t.templates.setting.hardwareNote}</Text>
    </Stack>
  );
}
