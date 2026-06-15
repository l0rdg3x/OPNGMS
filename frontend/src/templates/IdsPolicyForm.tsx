import {
  Button,
  Checkbox,
  CloseButton,
  Group,
  MultiSelect,
  NumberInput,
  Select,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useState } from "react";
import { useT } from "../i18n";
import { type RulesetRow, useIdsRulesets, useTenantDevices } from "./settingHooks";

export type PolicyBody = {
  description: string;
  enabled: string;
  prio: string;
  action: string[];
  rulesets: string[];
  content: Record<string, string[]>;
  new_action: string;
};

const ACTIONS = ["disable", "alert", "drop"];
const NEW_ACTIONS = ["default", "alert", "drop", "disable"];
// Mirror the backend _CONTENT_KEY_RE so a bad metadata key is caught before submit (clear message).
const CONTENT_KEY_RE = /^[A-Za-z0-9._-]+$/;

export function IdsPolicyForm(
  { value, onChange }: { value: PolicyBody; onChange: (v: PolicyBody) => void },
) {
  const t = useT();
  const { data: devices } = useTenantDevices();
  const [deviceId, setDeviceId] = useState<string>("");
  const [rows, setRows] = useState<RulesetRow[]>([]);
  const [loaded, setLoaded] = useState(false);
  const load = useIdsRulesets(deviceId);

  // advanced content editor draft (a single add-row)
  const [contentKey, setContentKey] = useState("");
  const [contentValues, setContentValues] = useState("");

  const deviceData = (devices ?? []).map((d) => ({ value: d.id, label: d.name }));
  // Offer enabled rulesets to pick (a policy can only reference enabled ones), but always keep any
  // already-selected ruleset in the option set so editing never silently drops it (Mantine MultiSelect
  // discards values that have no matching option). The apply-time check still refuses a disabled ruleset.
  const rulesetData = (() => {
    const opts = rows
      .filter((r) => r.enabled === "1")
      .map((r) => ({ value: r.filename, label: r.description || r.filename }));
    const present = new Set(opts.map((o) => o.value));
    for (const name of value.rulesets) {
      if (!present.has(name)) opts.push({ value: name, label: name });
    }
    return opts;
  })();

  async function loadRulesets() {
    try {
      const res = await load.mutateAsync();
      setRows(res);
      setLoaded(true);
    } catch {
      setRows([]);
      setLoaded(false);
      notifications.show({ color: "red", message: t.templates.idsPolicy.loadFailed });
    }
  }

  function addContent() {
    const key = contentKey.trim();
    if (!key) return;
    if (!CONTENT_KEY_RE.test(key)) {
      notifications.show({ color: "red", message: t.templates.idsPolicy.contentKeyInvalid });
      return;
    }
    const values = contentValues.split(",").map((s) => s.trim()).filter(Boolean);
    onChange({ ...value, content: { ...value.content, [key]: values } });
    setContentKey("");
    setContentValues("");
  }

  function removeContent(key: string) {
    const next = { ...value.content };
    delete next[key];
    onChange({ ...value, content: next });
  }

  return (
    <Stack>
      <TextInput
        label={t.templates.idsPolicy.description}
        required
        data-testid="idspolicy-description"
        value={value.description}
        onChange={(e) => onChange({ ...value, description: e.currentTarget.value })}
      />

      <Checkbox
        label={t.templates.idsPolicy.enabled}
        data-testid="idspolicy-enabled"
        checked={value.enabled === "1"}
        onChange={(e) => onChange({ ...value, enabled: e.currentTarget.checked ? "1" : "0" })}
      />

      <NumberInput
        label={t.templates.idsPolicy.prio}
        data-testid="idspolicy-prio"
        value={Number(value.prio) || 0}
        onChange={(v) => onChange({ ...value, prio: String(v ?? 0) })}
      />

      <MultiSelect
        label={t.templates.idsPolicy.action}
        data={ACTIONS}
        data-testid="idspolicy-action"
        value={value.action}
        onChange={(sel) => onChange({ ...value, action: sel })}
      />

      <Select
        label={t.templates.idsPolicy.newAction}
        data={NEW_ACTIONS}
        data-testid="idspolicy-newaction"
        value={value.new_action}
        onChange={(a) => onChange({ ...value, new_action: a ?? "alert" })}
        allowDeselect={false}
      />

      {deviceData.length === 0
        ? (
          <Text size="sm" c="dimmed" data-testid="idspolicy-no-device">
            {t.templates.idsPolicy.noDevice}
          </Text>
        )
        : (
          <>
            <Select
              label={t.templates.idsPolicy.referenceDevice}
              data={deviceData}
              data-testid="idspolicy-device"
              value={deviceId || null}
              onChange={(id) => setDeviceId(id ?? "")}
            />
            <Group>
              <Button
                data-testid="idspolicy-load"
                onClick={loadRulesets}
                loading={load.isPending}
                disabled={!deviceId}
              >
                {t.templates.idsPolicy.load}
              </Button>
            </Group>
          </>
        )}

      {!loaded
        ? (
          <Text size="sm" c="dimmed" data-testid="idspolicy-load-hint">
            {t.templates.idsPolicy.loadHint}
          </Text>
        )
        : rulesetData.length === 0
        ? (
          <Text size="sm" c="dimmed" data-testid="idspolicy-no-rulesets">
            {t.templates.idsPolicy.noRulesets}
          </Text>
        )
        : (
          <MultiSelect
            label={t.templates.idsPolicy.rulesets}
            data={rulesetData}
            data-testid="idspolicy-rulesets"
            searchable
            value={value.rulesets}
            onChange={(sel) => onChange({ ...value, rulesets: sel })}
          />
        )}

      <Stack gap="xs" data-testid="idspolicy-content">
        <Text size="sm">{t.templates.idsPolicy.content}</Text>
        {Object.entries(value.content).map(([k, vals]) => (
          <Group key={k} gap="xs" wrap="nowrap">
            <Text size="sm" style={{ flex: 1 }}>{`${k}: ${vals.join(", ")}`}</Text>
            <CloseButton aria-label={`remove ${k}`} onClick={() => removeContent(k)} />
          </Group>
        ))}
        <Group align="flex-end" gap="xs">
          <TextInput
            label={t.templates.idsPolicy.contentKey}
            data-testid="idspolicy-content-key"
            value={contentKey}
            onChange={(e) => setContentKey(e.currentTarget.value)}
          />
          <TextInput
            label={t.templates.idsPolicy.contentValues}
            data-testid="idspolicy-content-values"
            value={contentValues}
            onChange={(e) => setContentValues(e.currentTarget.value)}
            style={{ flex: 1 }}
          />
          <Button
            variant="default"
            data-testid="idspolicy-content-add"
            onClick={addContent}
            disabled={!contentKey.trim()}
          >
            {t.templates.idsPolicy.addContent}
          </Button>
        </Group>
      </Stack>

      <Text size="xs" c="dimmed" data-testid="idspolicy-note">{t.templates.idsPolicy.note}</Text>
    </Stack>
  );
}
