import { Button, Group, MultiSelect, Select, Stack, Switch, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useState } from "react";
import { useT } from "../i18n";
import { type SettingField, useIntrospectSetting, useSettingEndpoints, useTenantDevices } from "./settingHooks";

type SettingBody = { endpoint_key: string; payload: Record<string, string> };

function initialPayload(fields: SettingField[]): Record<string, string> {
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
  const introspect = useIntrospectSetting(deviceId);

  const endpointData = (endpoints ?? []).map((e) => ({ value: e.key, label: e.label }));
  const deviceData = (devices ?? []).map((d) => ({ value: d.id, label: d.name }));

  function setField(path: string, v: string) {
    onChange({ endpoint_key: value.endpoint_key, payload: { ...value.payload, [path]: v } });
  }

  async function loadFields() {
    try {
      const res = await introspect.mutateAsync(value.endpoint_key);
      setFields(res.fields);
      onChange({ endpoint_key: value.endpoint_key, payload: initialPayload(res.fields) });
    } catch {
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

      {fields.length === 0
        ? <Text size="sm" c="dimmed" data-testid="setting-load-hint">{t.templates.setting.loadHint}</Text>
        : (
          <Stack data-testid="setting-fields">
            {fields.map((field) => {
              const current = value.payload[field.path];
              if (field.control === "switch") {
                const checked = (current ?? String(field.value)) === "1";
                return (
                  <Switch
                    key={field.path}
                    label={field.label}
                    data-testid={`setting-field-${field.path}`}
                    checked={checked}
                    onChange={(e) => setField(field.path, e.currentTarget.checked ? "1" : "0")}
                  />
                );
              }
              if (field.control === "select") {
                return (
                  <Select
                    key={field.path}
                    label={field.label}
                    data={field.options ?? []}
                    data-testid={`setting-field-${field.path}`}
                    value={current ?? String(field.value)}
                    onChange={(key) => setField(field.path, key ?? "")}
                  />
                );
              }
              if (field.control === "multiselect") {
                const fallback = Array.isArray(field.value) ? field.value.join(",") : "";
                const selected = (current ?? fallback).split(",").filter(Boolean);
                return (
                  <MultiSelect
                    key={field.path}
                    label={field.label}
                    data={field.options ?? []}
                    data-testid={`setting-field-${field.path}`}
                    value={selected}
                    onChange={(keys) => setField(field.path, keys.join(","))}
                  />
                );
              }
              return (
                <TextInput
                  key={field.path}
                  label={field.label}
                  data-testid={`setting-field-${field.path}`}
                  value={current ?? String(field.value)}
                  onChange={(e) => setField(field.path, e.currentTarget.value)}
                />
              );
            })}
          </Stack>
        )}

      <Text size="xs" c="dimmed" data-testid="setting-hardware-note">{t.templates.setting.hardwareNote}</Text>
    </Stack>
  );
}
