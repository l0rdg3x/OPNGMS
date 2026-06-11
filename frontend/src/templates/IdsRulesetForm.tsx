import { Button, Group, MultiSelect, Select, Stack, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useState } from "react";
import { useT } from "../i18n";
import { type RulesetRow, useIdsRulesets, useTenantDevices } from "./settingHooks";

type IdsBody = { rulesets: string[] };

export function IdsRulesetForm(
  { value, onChange }: { value: IdsBody; onChange: (v: IdsBody) => void },
) {
  const t = useT();
  const { data: devices } = useTenantDevices();
  const [deviceId, setDeviceId] = useState<string>("");
  const [rows, setRows] = useState<RulesetRow[]>([]);
  const [loaded, setLoaded] = useState(false);
  const load = useIdsRulesets(deviceId);

  const deviceData = (devices ?? []).map((d) => ({ value: d.id, label: d.name }));
  const rulesetData = rows.map((r) => ({ value: r.filename, label: r.description || r.filename }));

  async function loadRulesets() {
    try {
      const res = await load.mutateAsync();
      setRows(res);
      setLoaded(true);
    } catch {
      setRows([]);
      setLoaded(false);
      notifications.show({ color: "red", message: t.templates.ids.loadFailed });
    }
  }

  return (
    <Stack>
      {deviceData.length === 0
        ? <Text size="sm" c="dimmed" data-testid="ids-no-device">{t.templates.ids.noDevice}</Text>
        : (
          <>
            <Select
              label={t.templates.ids.referenceDevice}
              data={deviceData}
              data-testid="ids-device"
              value={deviceId || null}
              onChange={(id) => setDeviceId(id ?? "")}
            />
            <Group>
              <Button
                data-testid="ids-load"
                onClick={loadRulesets}
                loading={load.isPending}
                disabled={!deviceId}
              >
                {t.templates.ids.load}
              </Button>
            </Group>
          </>
        )}

      {!loaded
        ? <Text size="sm" c="dimmed" data-testid="ids-load-hint">{t.templates.ids.loadHint}</Text>
        : rows.length === 0
          ? <Text size="sm" c="dimmed" data-testid="ids-no-rulesets">{t.templates.ids.noRulesets}</Text>
          : (
            <MultiSelect
              label={t.templates.ids.rulesets}
              data={rulesetData}
              data-testid="ids-rulesets"
              searchable
              value={value.rulesets}
              onChange={(sel) => onChange({ rulesets: sel })}
            />
          )}

      <Text size="xs" c="dimmed" data-testid="ids-note">{t.templates.ids.note}</Text>
    </Stack>
  );
}
