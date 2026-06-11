import { Button, Group, Select, Stack, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useState } from "react";
import { useT } from "../i18n";
import { AutoFormFields } from "./AutoFormFields";
import { initialPayload } from "./OpnsenseSettingForm";
import { type SettingField, useFirewallRuleModel, useTenantDevices } from "./settingHooks";

type RuleBody = { payload: Record<string, string> };

export function FirewallRuleForm(
  { value, onChange }: { value: RuleBody; onChange: (v: RuleBody) => void },
) {
  const t = useT();
  const { data: devices } = useTenantDevices();
  const [deviceId, setDeviceId] = useState<string>("");
  const [fields, setFields] = useState<SettingField[]>([]);
  const [loaded, setLoaded] = useState(false);
  const ruleModel = useFirewallRuleModel(deviceId);

  const deviceData = (devices ?? []).map((d) => ({ value: d.id, label: d.name }));

  function setField(path: string, v: string) {
    onChange({ payload: { ...value.payload, [path]: v } });
  }

  async function loadFields() {
    try {
      const res = await ruleModel.mutateAsync();
      const defaults = initialPayload(res.fields);
      const merged = { ...defaults, ...value.payload }; // saved values override device defaults
      setFields(res.fields);
      setLoaded(true);
      onChange({ payload: merged });
    } catch {
      setFields([]);
      setLoaded(false);
      notifications.show({ color: "red", message: t.templates.fw.loadFailed });
    }
  }

  return (
    <Stack>
      {deviceData.length === 0
        ? <Text size="sm" c="dimmed" data-testid="fw-no-device">{t.templates.fw.noDevice}</Text>
        : (
          <>
            <Select
              label={t.templates.fw.referenceDevice}
              data={deviceData}
              data-testid="fw-device"
              value={deviceId || null}
              onChange={(id) => setDeviceId(id ?? "")}
            />
            <Group>
              <Button
                data-testid="fw-load"
                onClick={loadFields}
                loading={ruleModel.isPending}
                disabled={!deviceId}
              >
                {t.templates.fw.load}
              </Button>
            </Group>
          </>
        )}

      {!loaded
        ? <Text size="sm" c="dimmed" data-testid="fw-load-hint">{t.templates.fw.loadHint}</Text>
        : (
          <Stack data-testid="fw-fields">
            <AutoFormFields fields={fields} payload={value.payload} onField={setField} testidPrefix="fw" />
          </Stack>
        )}

      <Text size="xs" c="dimmed" data-testid="fw-note">{t.templates.fw.note}</Text>
    </Stack>
  );
}
